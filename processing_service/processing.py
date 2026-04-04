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


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class TelemetryRow:
    time: datetime
    train_id: str
    health_score: float
    health_category: str
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
            json.dumps(self.params),
            json.dumps(self.route_info),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

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
            range_key, severity = classify_status(value, mdef)
            min_val, max_val = _metric_bounds(mdef)
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
            range_key   = "normal"
            severity    = "normal"
            range_label = _SEVERITY_RU["normal"]
            alert_msg   = ""
            rec         = ""
            min_val     = 0.0
            max_val     = 0.0

        enriched[key] = {
            "value":          value,
            "unit":           unit,
            "status":         _SEVERITY_RU.get(severity, severity),
            "range":          _SEVERITY_RU.get(range_key, range_key),
            "range_label":    range_label,
            "alert_message":  alert_msg,
            "recommendation": rec,
            "min":            min_val,
            "max":            max_val,
        }

    return enriched


# ---------------------------------------------------------------------------
# Health index
# ---------------------------------------------------------------------------

def compute_health(
    train_id: str,
    metrics: list[dict],
    metrics_definition: dict,
) -> tuple[float, str]:
    """
    Return (health_score 0–100, category letter).
    
    Starts at a baseline of 100 (perfect health).
    Subtracts defined penalties for metrics in warning/critical states.
    EMA smoothing is applied to the final score to prevent sudden jitter.
    """
    total_penalty = 0.0

    for m in metrics:
        key   = m["key"]
        value = float(m["current_value"])

        if key not in metrics_definition:
            continue

        mdef = metrics_definition[key]
        range_key, _ = classify_status(value, mdef)
        
        # Accumulate penalties based on the config (defaults to 0 if normal)
        total_penalty += mdef.get("penalties", {}).get(range_key, 0.0)

    # Base health is 100; subtract penalties, floor at 0
    raw_score = max(0.0, 100.0 - total_penalty)

    # Apply EMA to the overall health score
    ema_key = (train_id, "overall_health")
    prev = _ema_state.get(ema_key, raw_score)
    smoothed = _EMA_ALPHA * raw_score + (1 - _EMA_ALPHA) * prev
    _ema_state[ema_key] = smoothed

    # Determine category
    category = "RUN" # Fallback
    for letter, low, high in _CATEGORIES:
        if low <= smoothed <= high:
            category = letter
            break

    return round(smoothed, 2), category


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process(payload: dict) -> TelemetryRow:
    """
    Enrich a raw telemetry payload and return a TelemetryRow instance.

    Fields:
        time             — datetime (UTC)
        loco_id          — str
        health_score     — float  0–100
        health_category  — str    A–E
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

    enriched                = enrich_metrics(metrics, metrics_definition)
    health_score, category  = compute_health(train_id, metrics, metrics_definition)
    alert_count             = sum(1 for f in enriched.values() if f["status"] != _SEVERITY_RU["normal"])

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
        params=enriched,
        route_info=route_info,
    )
