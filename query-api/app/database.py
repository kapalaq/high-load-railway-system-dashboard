import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlmodel import Session, create_engine

from alembic import command
from alembic.config import Config

from app.config.base import CONFIG

logger = logging.getLogger("uvicorn")

DB_ENGINE = create_engine(CONFIG.DATABASE_URL)


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

    yield

    logger.info("Shutting down the application...")


def get_session():
    with Session(DB_ENGINE) as session:
        yield session
