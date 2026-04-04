import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI

from routes import health, telemetry_ws

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ingestion] %(message)s")
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STREAM_NAME = os.getenv("STREAM_NAME", "telemetry:raw")
STREAM_MAXLEN = int(os.getenv("STREAM_MAXLEN", "100000"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=False)
    app.state.stream_name = STREAM_NAME
    app.state.stream_maxlen = STREAM_MAXLEN
    logger.info("Redis pool created: %s", REDIS_URL)
    yield
    await app.state.redis.aclose()
    logger.info("Redis pool closed")


app = FastAPI(title="Ingestion Service", lifespan=lifespan)

app.get("/health")(health)
app.websocket("/ws/telemetry")(telemetry_ws)
