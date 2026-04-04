from pydantic import BaseModel


class GeoPoint(BaseModel):
    latitude: float
    longitude: float


class Stop(BaseModel):
    name: str
    distance_km: float
    status: str  # "passed" | "upcoming" | "current"
    latitude: float
    longitude: float


class RouteInfo(BaseModel):
    route_name: str
    total_distance_km: float
    current_position_km: float
    current: GeoPoint
    stops: list[Stop]


class Metric(BaseModel):
    key: str
    name_ru: str
    unit: str
    current_value: float


class TelemetryConfig(BaseModel):
    metrics: list[Metric]


class TelemetryMessage(BaseModel):
    train_id: str
    locomotive_type: str
    timestamp: str
    route_info: RouteInfo
    telemetry_config: TelemetryConfig
