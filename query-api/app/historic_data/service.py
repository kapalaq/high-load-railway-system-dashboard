from datetime import datetime

from sqlalchemy import text
from sqlmodel import Session

from .schemas import MetricDataPoint, MetricHistory, TelemetryRecord

_DISTANCE_QUERY = """
SELECT time, train_id, health_score, health_category, alert_count, params, route_info
FROM telemetry
WHERE train_id = :train_id
  AND (route_info->>'current_position_km') IS NOT NULL
ORDER BY ABS((route_info->>'current_position_km')::float - :distance) ASC
LIMIT 1
"""


_METRIC_RANGE_QUERY = """
SELECT time,
       (params -> :metric ->> 'value')::float AS value,
       params -> :metric ->> 'unit'            AS unit
FROM telemetry
WHERE train_id = :train_id
  AND time >= :from_dt
  AND time <= :to_dt
  AND params -> :metric IS NOT NULL
ORDER BY time ASC
"""


def get_metric_history(
    session: Session,
    train_id: str,
    metric: str,
    from_dt: datetime,
    to_dt: datetime,
) -> MetricHistory:
    rows = session.execute(
        text(_METRIC_RANGE_QUERY),
        {"train_id": train_id, "metric": metric, "from_dt": from_dt, "to_dt": to_dt},
    ).fetchall()

    data = [MetricDataPoint(time=row.time, value=row.value) for row in rows]
    return MetricHistory(
        train_id=train_id,
        metric=metric,
        unit=rows[0].unit if rows else None,
        from_=from_dt,
        to=to_dt,
        data=data,
        trend=_compute_trend(data),
    )


def _compute_trend(data: list[MetricDataPoint]) -> str | None:
    values = [p.value for p in data if p.value is not None]
    if len(values) < 2:
        return None
    # Linear regression slope over the data points
    n = len(values)
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    numerator = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    if denominator == 0:
        return None
    slope = numerator / denominator
    return "растет" if slope > 0 else "снижается"


def get_telemetry_by_distance(
    session: Session,
    train_id: str,
    distance: float,
) -> TelemetryRecord | None:
    row = session.execute(
        text(_DISTANCE_QUERY),
        {"train_id": train_id, "distance": distance},
    ).fetchone()

    if row is None:
        return None
    return TelemetryRecord(
        time=row.time,
        train_id=row.train_id,
        health_score=row.health_score,
        health_category=row.health_category.strip() if row.health_category else None,
        alert_count=row.alert_count,
        params=row.params,
        route_info=row.route_info,
    )
