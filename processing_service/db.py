import os

import asyncpg

DB_URL = os.environ.get("DB_URL", "postgresql://user:password@localhost:5432/locomotive")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS telemetry (
    time             TIMESTAMPTZ      NOT NULL,
    loco_id          TEXT             NOT NULL,
    health_score     DOUBLE PRECISION,
    health_category  CHAR(255),
    alert_count      INTEGER,
    params           JSONB,
    route_info       JSONB
);
"""

CREATE_HYPERTABLE_SQL = """
SELECT create_hypertable('telemetry', 'time', if_not_exists => TRUE);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_telemetry_loco ON telemetry (loco_id, time DESC);
"""

INSERT_SQL = """
INSERT INTO telemetry (time, loco_id, health_score, health_category, alert_count, params, route_info)
VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
"""


async def get_connection() -> asyncpg.Connection:
    return await asyncpg.connect(DB_URL)


async def init_db(conn: asyncpg.Connection) -> None:
    await conn.execute(CREATE_TABLE_SQL)
    try:
        await conn.execute(CREATE_HYPERTABLE_SQL)
    except Exception as e:
        if "already a hypertable" not in str(e):
            raise
    await conn.execute(CREATE_INDEX_SQL)
    print("[db] TimescaleDB schema ready")
