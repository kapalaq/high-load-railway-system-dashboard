from sqlalchemy import text
from sqlmodel import Session

from .schemas import TelemetryRecord

_DISTANCE_QUERY = """
SELECT time, train_id, health_score, health_category, alert_count, params, route_info
FROM telemetry
WHERE train_id = :train_id
  AND (route_info->>'current_position_km') IS NOT NULL
ORDER BY ABS((route_info->>'current_position_km')::float - :distance) ASC
LIMIT 1
"""


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
