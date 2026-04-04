import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.auth.dependencies import get_current_user
from app.database import TIMESCALE_ENGINE
from .schemas import MetricHistory, TelemetryRecord
from .service import get_metric_history, get_telemetry_by_distance

logger = logging.getLogger("uvicorn")

historic_router = APIRouter(prefix="/api/historic", tags=["historic"])


def get_timescale_session():
    with Session(TIMESCALE_ENGINE) as session:
        yield session


@historic_router.get("/telemetry/{train_id}/metrics/{metric}", response_model=MetricHistory)
def query_metric_history(
    train_id: str,
    metric: str,
    from_dt: datetime = Query(..., alias="from", description="Start of time range (ISO 8601)"),
    to_dt: datetime = Query(..., alias="to", description="End of time range (ISO 8601)"),
    session: Session = Depends(get_timescale_session),
    _current_user=Depends(get_current_user),
):
    logger.info("metric history request: train_id=%s metric=%s from=%s to=%s", train_id, metric, from_dt, to_dt)
    if from_dt >= to_dt:
        raise HTTPException(status_code=400, detail="'from' must be earlier than 'to'")
    try:
        result = get_metric_history(session, train_id, metric, from_dt, to_dt)
        logger.info("metric history result: %d data points, trend=%s", len(result.data), result.trend)
        return result
    except Exception as e:
        logger.exception("metric history failed: %s", e)
        raise


@historic_router.get("/telemetry/{train_id}", response_model=TelemetryRecord)
def query_telemetry(
    train_id: str,
    distance: float = Query(..., description="Route distance in km to find the closest record to"),
    session: Session = Depends(get_timescale_session),
    _current_user=Depends(get_current_user),
):
    record = get_telemetry_by_distance(session, train_id, distance)
    if record is None:
        raise HTTPException(status_code=404, detail="No telemetry found for this train")
    return record
