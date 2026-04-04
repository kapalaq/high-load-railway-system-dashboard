from datetime import datetime

from sqlalchemy import text
from sqlmodel import Session

from .schemas import TelemetryRecord

_RANGE_QUERY = """
SELECT time, train_id, health_score, health_category, alert_count, params, route_info
FROM telemetry
WHERE train_id = :train_id
  AND time >= :from_time
  AND time <= :to_time
ORDER BY time DESC
LIMIT :limit
OFFSET :offset
"""

_LATEST_QUERY = """
SELECT time, train_id, health_score, health_category, alert_count, params, route_info
FROM telemetry
WHERE train_id = :train_id
ORDER BY time DESC
LIMIT :limit
OFFSET :offset
"""


def get_telemetry(
    session: Session,
    train_id: str,
    from_time: datetime | None,
    to_time: datetime | None,
    limit: int = 100,
    offset: int = 0,
) -> list[TelemetryRecord]:
    if from_time is None and to_time is None:
        rows = session.execute(
            text(_LATEST_QUERY),
            {"train_id": train_id, "limit": limit, "offset": offset},
        ).fetchall()
    else:
        rows = session.execute(
            text(_RANGE_QUERY),
            {
                "train_id": train_id,
                "from_time": from_time,
                "to_time": to_time,
                "limit": limit,
                "offset": offset,
            },
        ).fetchall()

    return [
        TelemetryRecord(
            time=row.time,
            train_id=row.train_id,
            health_score=row.health_score,
            health_category=row.health_category.strip() if row.health_category else None,
            alert_count=row.alert_count,
            params=row.params,
            route_info=row.route_info,
        )
        for row in rows
    ]
