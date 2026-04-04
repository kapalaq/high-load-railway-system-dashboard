import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import from_url as redis_from_url
from sqlmodel import Session, create_engine

from alembic import command
from alembic.config import Config

from app.config.base import CONFIG
from app.websocket.manager import ConnectionManager

logger = logging.getLogger("uvicorn")

DB_ENGINE = create_engine(CONFIG.DATABASE_URL)

PUBSUB_CHANNEL = "metrics:live"


async def _redis_dispatcher(app: FastAPI) -> None:
    redis = app.state.redis
    manager: ConnectionManager = app.state.manager
    async with redis.pubsub() as ps:
        await ps.subscribe(PUBSUB_CHANNEL)
        async for msg in ps.listen():
            if msg["type"] != "message":
                continue
            try:
                train_id = json.loads(msg["data"]).get("train_id")
                if train_id:
                    await manager.broadcast(train_id, msg["data"])
            except Exception as exc:
                logger.warning("dispatcher error: %s", exc)


@asynccontextmanager
async def db_lifespan(app: FastAPI):
    logger.info("Starting up the application...")

    if CONFIG.RUN_MIGRATIONS_UPON_LAUNCH:
        logger.info("Running alembic upgrade head...")
        try:
            alembic_cfg = Config("alembic.ini")
            command.upgrade(alembic_cfg, "head")
        except Exception as e:
            logger.error("Error running database migrations: %s", e)
            raise SystemError(f"Migrations failed: {str(e)}") from e

    app.state.redis = redis_from_url(CONFIG.REDIS_URL, decode_responses=True)
    app.state.manager = ConnectionManager()
    dispatcher = asyncio.create_task(_redis_dispatcher(app))
    logger.info("Redis dispatcher started on channel '%s'", PUBSUB_CHANNEL)

    yield

    dispatcher.cancel()
    await asyncio.gather(dispatcher, return_exceptions=True)
    await app.state.redis.aclose()
    logger.info("Shutting down the application...")


def get_session():
    with Session(DB_ENGINE) as session:
        yield session
