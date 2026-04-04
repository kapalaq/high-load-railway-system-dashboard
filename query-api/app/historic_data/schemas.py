from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TelemetryRecord(BaseModel):
    time: datetime
    train_id: str
    health_score: float | None
    health_category: str | None
    alert_count: int | None
    params: dict[str, Any] | None
    route_info: dict[str, Any] | None
