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


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class TelemetryRow:
    time: datetime
    loco_id: str
    health_score: float
    health_category: str
    alert_count: int
    params: dict       # enriched metrics — plain dict, easy to inspect
    route_info: dict   # raw route_info — plain dict

    def db_tuple(self) -> tuple:
        """Ready-to-insert tuple for asyncpg executemany."""
        return (
            self.time,
            self.loco_id,
            self.health_score,
            self.health_category,
            self.alert_count,
            self.params,
            self.route_info,
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
        all_vals.extend(rng)
    return min(all_vals), max(all_vals)


def _normalize(value: float, min_val: float, max_val: float) -> float:
    span = max_val - min_val
    if span == 0:
        return 0.0
    return max(0.0, min(1.0, (value - min_val) / span))


def classify_status(value: float, metric_def: dict) -> str:
    """Return 'ok', 'warning', or 'critical' based on defined ranges."""
    ranges = metric_def["ranges"]
    crit = ranges["critical"]
    if crit[0] <= value <= crit[1]:
        return "critical"
    warn = ranges["warning"]
    if warn[0] <= value <= warn[1]:
        return "warning"
    return "ok"


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
            mdef  = metrics_definition[key]
            level = classify_status(value, mdef)

            if level == "critical":
                alert_msg = mdef["critical_message"]
                rec       = mdef["critical_recommendation"]
            elif level == "warning":
                alert_msg = mdef["warning_message"]
                rec       = mdef["warning_recommendation"]
            else:
                alert_msg = ""
                rec       = ""
        else:
            level     = "ok"
            alert_msg = ""
            rec       = ""

        enriched[key] = {
            "value":           value,
            "unit":            unit,
            "status":          level,
            "alert_message":   alert_msg,
            "recommendation":  rec,
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
    Return (health_score 0–100, category letter A–E).

    Each known metric contributes equally (weight=1).
    EMA-smoothed normalised values stabilise the score.
    Per-metric penalties are subtracted for warning/critical alerts.
    """
    smoothed_sum  = 0.0
    total_metrics = 0
    total_penalty = 0.0

    for m in metrics:
        key   = m["key"]
        value = float(m["current_value"])

        if key not in metrics_definition:
            continue

        mdef             = metrics_definition[key]
        min_val, max_val = _metric_bounds(mdef)
        norm             = _normalize(value, min_val, max_val)

        ema_key             = (train_id, key)
        prev                = _ema_state.get(ema_key, norm)
        smoothed            = _EMA_ALPHA * norm + (1 - _EMA_ALPHA) * prev
        _ema_state[ema_key] = smoothed

        smoothed_sum  += smoothed
        total_metrics += 1

        level          = classify_alert(value, mdef)
        total_penalty += mdef["penalties"].get(level, 0)

    raw_score = (smoothed_sum / total_metrics * 100) if total_metrics > 0 else 0.0
    final     = max(0.0, raw_score - total_penalty)

    category = "E"
    for letter, low, high in _CATEGORIES:
        if low <= final <= high:
            category = letter
            break

    return round(final, 2), category


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
    alert_count             = sum(1 for f in enriched.values() if f["status"] != "ok")

    try:
        time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except Exception:
        time = datetime.now(tz=timezone.utc)

    return TelemetryRow(
        time=time,
        loco_id=train_id,
        health_score=health_score,
        health_category=category,
        alert_count=alert_count,
        params=enriched,
        route_info=route_info,
    )
