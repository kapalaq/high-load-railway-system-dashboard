from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.auth.dependencies import get_current_user
from app.database import TIMESCALE_ENGINE
from .schemas import TelemetryRecord
from .service import get_telemetry_by_distance
from fastapi import HTTPException

historic_router = APIRouter(prefix="/api/historic", tags=["historic"])


def get_timescale_session():
    with Session(TIMESCALE_ENGINE) as session:
        yield session


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
