import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ingestion] %(message)s")
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STREAM_NAME = os.getenv("STREAM_NAME", "telemetry:raw")
STREAM_MAXLEN = int(os.getenv("STREAM_MAXLEN", "100000"))


class Stop(BaseModel):
    name: str
    distance_km: float
    status: str  # "passed" | "upcoming" | "current"


class RouteInfo(BaseModel):
    route_name: str
    total_distance_km: float
    current_position_km: float
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=False)
    logger.info("Redis pool created: %s", REDIS_URL)
    yield
    await app.state.redis.aclose()
    logger.info("Redis pool closed")


app = FastAPI(title="Ingestion Service", lifespan=lifespan)


@app.get("/health")
async def health(request: Request):
    try:
        await request.app.state.redis.ping()
        return {"status": "ok", "redis": "connected"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "redis": str(e)})


@app.websocket("/ws/telemetry")
async def telemetry_ws(websocket: WebSocket):
    await websocket.accept()
    redis_client = websocket.app.state.redis
    client = websocket.client
    logger.info("Client connected: %s:%s", client.host, client.port)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                TelemetryMessage.model_validate_json(raw)
            except ValidationError as e:
                logger.warning("Validation error from %s:%s — %s", client.host, client.port, e)
                continue  # drop bad message, keep connection alive

            await redis_client.xadd(
                STREAM_NAME,
                {"payload": raw.encode()},
                maxlen=STREAM_MAXLEN,
                approximate=True,
            )
    except WebSocketDisconnect:
        logger.info("Client disconnected: %s:%s", client.host, client.port)
    except Exception as e:
        logger.error("Unexpected error from %s:%s — %s", client.host, client.port, e)
