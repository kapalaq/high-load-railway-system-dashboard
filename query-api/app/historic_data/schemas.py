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


class MetricDataPoint(BaseModel):
    time: datetime
    value: float | None


class MetricHistory(BaseModel):
    train_id: str
    metric: str
    unit: str | None
    from_: datetime
    to: datetime
    data: list[MetricDataPoint]
    trend: str | None  # "растет" | "снижается" | None if insufficient data
