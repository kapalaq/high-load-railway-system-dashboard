from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.auth.dependencies import get_current_user
from app.database import TIMESCALE_ENGINE
from .schemas import TelemetryRecord
from .service import get_telemetry

historic_router = APIRouter(prefix="/api/historic", tags=["historic"])


def get_timescale_session():
    with Session(TIMESCALE_ENGINE) as session:
        yield session


@historic_router.get("/telemetry/{train_id}", response_model=list[TelemetryRecord])
def query_telemetry(
    train_id: str,
    from_time: datetime | None = Query(None, alias="from", description="Start of time range (ISO 8601)"),
    to_time: datetime | None = Query(None, alias="to", description="End of time range (ISO 8601)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_timescale_session),
    _current_user=Depends(get_current_user),
):
    return get_telemetry(session, train_id, from_time, to_time, limit, offset)
