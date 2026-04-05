"""
processing.py — telemetry enrichment and health index computation.

Loads config.json for per-loco-type metric definitions (ranges, penalties,
alert messages, recommendations) and global settings (ema_alpha, categories).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", Path(__file__).parent / "config.json"))


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return json.load(f)


_CONFIG: dict = _load_config()

_EMA_ALPHA: float = _CONFIG["global"]["ema_alpha"]
_CATEGORIES: list[tuple[str, float, float]] = [
    (letter, low, high) for letter, low, high in _CONFIG["categories"]
]
_ema_state: dict[tuple[str, str], float] = {}

_SEVERITY_RU: dict[str, str] = {
    "normal":   "норма",
    "warning":  "предупреждение",
    "critical": "критично",
}

# Metrics included in the "Состояние Узлов" aggregate: key → Russian display name
_NODE_STATUS_KEYS: dict[str, str] = {
    "pressure_brake":     "Давление тормозной магистрали",
    "tractive_force":     "Тяговое усилие",
    "temp_oil":           "Температура масла",
    "temp_motor":         "Температура двигатель",
    "temp_air":           "Температура воздуха",
    "pressure_main_tank": "Давление главного резервуара",
}


@dataclass
class TelemetryRow:
    time: datetime
    train_id: str
    health_score: float
    health_category: str
    top_impacts: list  # top-5 metrics by health penalty, descending
    alert_count: int
    params: dict       # enriched metrics — plain dict, easy to inspect
    route_info: dict   # raw route_info — plain dict

    def db_tuple(self) -> tuple:
        """Ready-to-insert tuple for asyncpg executemany."""
        return (
            self.time,
            self.train_id,
            self.health_score,
            self.health_category,
            self.alert_count,
            json.dumps(self.top_impacts),
            json.dumps(self.params),
            json.dumps(self.route_info),
        )


def _extract_loco_type(train_id: str) -> str:
    """'KZ8A-L001' → 'KZ8A', 'TE33A-L006' → 'TE33A'"""
    parts = train_id.split("-L")
    return parts[0] if len(parts) > 1 else train_id.split("-")[0]


def _metric_bounds(metric_def: dict) -> tuple[float, float]:
    """Overall (min, max) across all ranges — used for normalisation."""
    all_vals: list[float] = []
    for rng in metric_def["ranges"].values():
        bounds = rng["bounds"] if isinstance(rng, dict) else rng
        all_vals.extend(bounds)
    return min(all_vals), max(all_vals)


def _normal_bounds(metric_def: dict) -> tuple[float, float]:
    """(min, max) across all ranges whose severity is 'normal'."""
    normal_vals: list[float] = []
    for key, rng in metric_def["ranges"].items():
        if isinstance(rng, dict):
            if rng.get("severity", "normal") == "normal":
                normal_vals.extend(rng["bounds"])
        else:
            # Legacy list format: severity == range key name
            if key == "normal":
                normal_vals.extend(rng)
    if not normal_vals:
        return _metric_bounds(metric_def)
    return min(normal_vals), max(normal_vals)


def _normalize(value: float, min_val: float, max_val: float) -> float:
    span = max_val - min_val
    if span == 0:
        return 0.0
    return max(0.0, min(1.0, (value - min_val) / span))


def classify_status(value: float, metric_def: dict) -> tuple[str, str]:
    """Return (range_key, severity) based on defined ranges.

    Supports two range formats:
    - Legacy:  {"normal": [min, max], ...}              → severity == range_key
    - Named:   {"name": {"bounds": [min, max], "severity": "warning", ...}}
    """
    for key, val in metric_def["ranges"].items():
        if isinstance(val, list):
            if val[0] <= value <= val[1]:
                return key, key
        else:
            b = val["bounds"]
            if b[0] <= value <= b[1]:
                return key, val["severity"]
    return "normal", "normal"


def enrich_metrics(metrics: list[dict], metrics_definition: dict) -> dict[str, dict]:
    """
    Return enriched feature dict keyed by metric key.

    Known metrics get status classification + message from config.
    Unknown metrics pass through with status='ok' and empty strings.
    """
    enriched: dict[str, dict] = {}

    for m in metrics:
        key   = m["key"]
        value = m["current_value"]
        unit  = m["unit"]

        if key in metrics_definition:
            mdef             = metrics_definition[key]
            name             = mdef.get("label", m.get("name_ru", key))
            range_key, severity = classify_status(value, mdef)
            min_val, max_val = _metric_bounds(mdef)
            norm_min, norm_max = _normal_bounds(mdef)
            range_def        = mdef["ranges"][range_key]

            if isinstance(range_def, list):
                # Legacy format — messages stored as "{severity}_message" on metric
                if severity != "normal":
                    alert_msg = mdef.get(f"{severity}_message", "")
                    rec       = mdef.get(f"{severity}_recommendation", "")
                else:
                    alert_msg = ""
                    rec       = ""
                range_label = _SEVERITY_RU.get(severity, severity)
            else:
                # Named range format — messages stored inline on the range object
                alert_msg   = range_def.get("message", "")
                rec         = range_def.get("recommendation", "")
                range_label = range_def.get("label", _SEVERITY_RU.get(range_key, range_key))
        else:
            name        = m.get("name_ru", key)
            range_key   = "normal"
            severity    = "normal"
            range_label = _SEVERITY_RU["normal"]
            alert_msg   = ""
            rec         = ""
            min_val     = 0.0
            max_val     = 0.0
            norm_min    = 0.0
            norm_max    = 0.0

        enriched[key] = {
            "name":           name,
            "value":          value,
            "unit":           unit,
            "status":         _SEVERITY_RU.get(severity, severity),
            "range":          _SEVERITY_RU.get(range_key, range_key),
            "range_label":    range_label,
            "alert_message":  alert_msg,
            "recommendation": rec,
            "min":            min_val,
            "max":            max_val,
            "norm_min":       max(norm_min, 0.1 * norm_max),
            "norm_max":       norm_max,
        }

    return enriched


# Each entry: (lat_min, lat_max, lon_min, lon_max, status, recommendation)
_TERRAIN_ZONES: list[tuple[float, float, float, float, str, str]] = [
    # Горные участки вблизи Алматы (хребты Заилийского Алатау)
    (43.0, 44.2, 75.5, 78.0, "критично",
     "Горный участок", "немедленно снизьте скорость до 60 км/ч"),
    # Предгорная зона между Алматы и Балхашем
    (44.2, 46.0, 74.0, 78.0, "предупреждение",
     "Предгорный участок", "снизьте скорость"),
    # Промышленная зона Карагандинского угольного бассейна
    (49.5, 50.2, 72.5, 73.8, "предупреждение",
     "Промышленная зона", "соблюдайте осторожность"),
    # Степной участок с высоким ветром (открытая равнина севернее Астаны)
    (51.5, 53.0, 70.0, 73.0, "предупреждение",
     "Открытая степь", "возможен сильный боковой ветер"),
]

_STOP_WARNING_KM  = 30   # предупреждение при приближении к станции
_STOP_CRITICAL_KM = 10   # критично при подъезде к станции


import math


def point_to_rectangle_distance(
    point_lat: float,
    point_lon: float,
    rect_min_lat: float,
    rect_min_lon: float,
    rect_max_lat: float,
    rect_max_lon: float,
) -> float:
    """
    Returns the shortest distance in kilometres from a point to a rectangle.
    Returns 0.0 if the point is inside the rectangle.
    """
    # Clamp point to nearest location on/inside the rectangle
    nearest_lat = max(rect_min_lat, min(rect_max_lat, point_lat))
    nearest_lon = max(rect_min_lon, min(rect_max_lon, point_lon))

    # Haversine distance from point to nearest rectangle point
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [point_lat, point_lon, nearest_lat, nearest_lon])
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _route_terrain_status(current: dict, stops: list[dict], position_km: float) -> tuple[str, str]:
    """Return (status, recommendation) based on proximity to stops and terrain zones."""
    lat = current.get("latitude",  0.0)
    lon = current.get("longitude", 0.0)

    # 1. Proximity to upcoming/current stops (highest priority)
    for stop in stops:
        if stop.get("status") in ("текущая", "впереди"):
            dist_km = abs(position_km - stop["distance_km"])
            if dist_km <= _STOP_CRITICAL_KM:
                return (
                    "Заезд на станцию",
                    "критично",
                    f"Снизьте скорость до 40 км/ч",
                )
            if dist_km <= _STOP_WARNING_KM:
                return (
                    "Подъезд к станции",
                    "предупреждение",
                    f"Cнизьте скорость до 80 км/ч",
                )

    # 2. Terrain zone check
    for lat_min, lat_max, lon_min, lon_max, status, name, rec in _TERRAIN_ZONES:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return lat_min, lat_max, lon_min, lon_max, name, status, rec

    return 0, 0, 0, 0, "Обычная ситуация", "норма", "Нет сообщений"


def enrich_route(route_info: dict, speed: float) -> None:
    """Mutates route_info in-place, adding an 'info' sub-dict with enriched fields."""
    position_km      = route_info.get("current_position_km", 0.0)
    total_km         = route_info.get("total_distance_km",   0.0)
    current          = route_info.get("current", {})
    stops            = route_info.get("stops",   [])

    route_info["distance_left_km"] = max(0.0, total_km - position_km)
    route_info["time_left_h"]      = round(route_info["distance_left_km"] / speed, 2) if speed > 0 else None

    lat_min, lat_max, lon_min, lon_max, name, status, recommendation = _route_terrain_status(current, stops, position_km)

    distance_left_km = point_to_rectangle_distance(
        route_info["current"]["latitude"],
        route_info["current"]["longitude"],
        lat_min, lat_max, lon_min, lon_max,
    ) if lat_min != lat_max != lon_min != lon_max != 0 else 0
    time_left_h = round(distance_left_km / speed, 2) if speed > 0 else 0

    route_info["info"] = {
        "distance_left_km": round(distance_left_km, 2),
        "time_left_h":      time_left_h,
        "name":             name,
        "status":           status,
        "recommendation":   recommendation,
    }


def compute_health(
    train_id: str,
    metrics: list[dict],
    metrics_definition: dict,
) -> tuple[float, str, list]:
    """
    Return (health_score 0–100, category letter, top_impacts).

    Starts at a baseline of 100 (perfect health).
    Subtracts defined penalties for metrics in warning/critical states.
    EMA smoothing is applied to the final score to prevent sudden jitter.

    top_impacts — top-5 metrics by penalty, each entry:
        {"metric": key, "status": string, "impact": float}
        where impact = (penalty / max_penalty_for_metric) * 100
    """
    total_penalty = 0.0
    per_metric: list[tuple[str, float, float]] = []  # (key, status, max_penalty)

    for m in metrics:
        key   = m["key"]
        value = float(m["current_value"])

        if key not in metrics_definition:
            continue

        mdef      = metrics_definition[key]
        range_key, severity = classify_status(value, mdef)
        penalties = mdef.get("penalties", {})
        penalty   = penalties.get(range_key, 0.0)
        max_pen   = max(penalties.values()) if penalties else 0.0

        total_penalty += penalty
        if penalty > 0:
            per_metric.append((key, severity, penalty, max_pen))
    
    if len(per_metric) == 0:
        fallback = []
        for m in metrics:
            key = m["key"]
            if key not in metrics_definition:
                continue
            penalties = metrics_definition[key].get("penalties", {})
            max_pen = max(penalties.values()) if penalties else 0.0
            if max_pen > 0:
                fallback.append((key, "normal", max_pen, 100))
        per_metric = sorted(fallback, key=lambda x: x[2], reverse=True)[:5]

    raw_score = max(0.0, 100.0 - total_penalty)

    ema_key = (train_id, "overall_health")
    prev    = _ema_state.get(ema_key, raw_score)
    smoothed = _EMA_ALPHA * raw_score + (1 - _EMA_ALPHA) * prev
    _ema_state[ema_key] = smoothed

    category = "БЕГИ"
    for letter, low, high in _CATEGORIES:
        if low <= smoothed <= high:
            category = letter
            break

    top5 = sorted(per_metric, key=lambda x: x[1], reverse=True)[:5]
    top_impacts = [
        {
            "metric":  key,
            "status":  severity,
            "impact":  round((penalty / max_pen) * 100, 1) if max_pen else 0.0,
        }
        for key, severity, penalty, max_pen in top5
    ]

    return round(smoothed, 2), category, top_impacts


def process(payload: dict) -> TelemetryRow:
    """
    Enrich a raw telemetry payload and return a TelemetryRow instance.

    Fields:
        time             — datetime (UTC)
        loco_id          — str
        health_score     — float  0–100
        health_category  — str    run-normal
        alert_count      — int
        params           — dict   enriched metrics
        route_info       — dict   raw route info (empty dict if absent)
    """
    train_id      = payload["train_id"]
    timestamp_str = payload["timestamp"]
    metrics       = payload["telemetry_config"]["metrics"]
    route_info    = payload.get("route_info") or {}

    loco_type          = _extract_loco_type(train_id)
    loco_data          = _CONFIG["locomotives"].get(loco_type, {})
    metrics_definition = loco_data.get("metrics", {})

    enriched                            = enrich_metrics(metrics, metrics_definition)
    health_score, category, top_impacts = compute_health(train_id, metrics, metrics_definition)
    alert_count                         = sum(1 for f in enriched.values() if f["status"] != _SEVERITY_RU["normal"])
    enrich_route(route_info, enriched["speed"]["value"])

    enriched["system_condition"] = {
        "name": "Состояние Узлов",
        "value": [
            {"name": label, "value": enriched[key]["status"]} if key in enriched else _SEVERITY_RU["normal"]
            for key, label in _NODE_STATUS_KEYS.items()
        ],
    }

    try:
        time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except Exception:
        time = datetime.now(tz=timezone.utc)

    return TelemetryRow(
        time=time,
        train_id=train_id,
        health_score=health_score,
        health_category=category,
        alert_count=alert_count,
        top_impacts=top_impacts,
        params=enriched,
        route_info=route_info,
    )
