"""
processing.py — telemetry enrichment and health index computation.

Uses train_data_contained.json for per-loco-type metric definitions.
Each metric has ranges (normal/warning/critical) and per-severity penalties.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_DATA_PATH = Path(os.environ.get("TRAIN_DATA_PATH", Path(__file__).parent / "train_data_contained.json"))


def _load_data() -> dict:
    with open(_DATA_PATH) as f:
        return json.load(f)


_TRAIN_DATA: dict = _load_data()

_CATEGORIES = [
    ("A", 90, 100),
    ("B", 75, 90),
    ("C", 50, 75),
    ("D", 25, 50),
    ("E",  0, 25),
]

_EMA_ALPHA = 0.2
_ema_state: dict[tuple[str, str], float] = {}


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


def classify_alert(value: float, metric_def: dict) -> str:
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

    Known metrics get alert classification + message.
    Unknown metrics pass through with alert='ok' and empty strings.
    """
    enriched: dict[str, dict] = {}

    for m in metrics:
        key   = m["key"]
        value = m["current_value"]
        unit  = m["unit"]

        if key in metrics_definition:
            mdef  = metrics_definition[key]
            level = classify_alert(value, mdef)
            label = mdef.get("label", key)

            if level == "critical":
                alert_msg = f"CRITICAL: {label}"
                rec       = f"Take immediate action — {label} is in critical range."
            elif level == "warning":
                alert_msg = f"WARNING: {label}"
                rec       = f"Monitor closely — {label} is in warning range."
            else:
                alert_msg = ""
                rec       = ""
        else:
            level     = "ok"
            alert_msg = ""
            rec       = ""

        enriched[key] = {
            "value":          value,
            "unit":           unit,
            "alert":          level,
            "alert_message":  alert_msg,
            "recommendation": rec,
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

        mdef            = metrics_definition[key]
        min_val, max_val = _metric_bounds(mdef)
        norm            = _normalize(value, min_val, max_val)

        ema_key              = (train_id, key)
        prev                 = _ema_state.get(ema_key, norm)
        smoothed             = _EMA_ALPHA * norm + (1 - _EMA_ALPHA) * prev
        _ema_state[ema_key]  = smoothed

        smoothed_sum  += smoothed
        total_metrics += 1

        level         = classify_alert(value, mdef)
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

def process(payload: dict) -> dict:
    """
    Enrich a raw telemetry payload and return a flat dict for asyncpg INSERT.

    Return shape:
        {
            "time":             datetime (UTC),
            "loco_id":          str,
            "health_score":     float,
            "health_category":  str,
            "alert_count":      int,
            "params":           str   # JSON string for JSONB column
        }
    """
    train_id      = payload["train_id"]
    timestamp_str = payload["timestamp"]          # ISO-8601, e.g. "2026-04-04T12:00:00Z"
    metrics       = payload["telemetry_config"]["metrics"]  # list[{key, name_ru, unit, current_value}]

    loco_type          = _extract_loco_type(train_id)
    loco_data          = _TRAIN_DATA.get(loco_type, {})
    metrics_definition = loco_data.get("metrics_definition", {})

    enriched                = enrich_metrics(metrics, metrics_definition)
    health_score, category  = compute_health(train_id, metrics, metrics_definition)
    alert_count             = sum(1 for f in enriched.values() if f["alert"] != "ok")

    try:
        time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except Exception:
        time = datetime.now(tz=timezone.utc)

    return {
        "time":            time,
        "loco_id":         train_id,
        "health_score":    health_score,
        "health_category": category,
        "alert_count":     alert_count,
        "params":          json.dumps(enriched),
    }
