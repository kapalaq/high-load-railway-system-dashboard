import os
import json
import asyncio
import asyncpg
import redis.asyncio as aio_redis

from processing import process

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
STREAM_NAME = os.environ.get("STREAM_NAME", "telemetry:raw")
BLOCK_MS = 2000  # block up to 2s waiting for new messages
DB_URL      = os.environ.get("DB_URL", "postgresql://user:password@localhost:5432/locomotive")

STREAM_NAME = "telemetry:raw"
GROUP_NAME  = "processing-group"
CONSUMER_ID = "processor-1"
BLOCK_MS    = 2000

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS telemetry (
    time             TIMESTAMPTZ      NOT NULL,
    loco_id          TEXT             NOT NULL,
    health_score     DOUBLE PRECISION,
    health_category  CHAR(1),
    alert_count      INTEGER,
    params           JSONB
);
"""

CREATE_HYPERTABLE_SQL = """
SELECT create_hypertable('telemetry', 'time', if_not_exists => TRUE);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_telemetry_loco ON telemetry (loco_id, time DESC);
"""

INSERT_SQL = """
INSERT INTO telemetry (time, loco_id, health_score, health_category, alert_count, params)
VALUES ($1, $2, $3, $4, $5, $6::jsonb)
"""


async def init_db(conn: asyncpg.Connection) -> None:
    await conn.execute(CREATE_TABLE_SQL)
    try:
        await conn.execute(CREATE_HYPERTABLE_SQL)
    except Exception as e:
        if "already a hypertable" not in str(e):
            raise
    await conn.execute(CREATE_INDEX_SQL)
    print("[processing] TimescaleDB schema ready")


async def main():
    r = aio_redis.from_url(REDIS_URL, decode_responses=True)

    try:
        await r.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
        print(f"[processing] created consumer group '{GROUP_NAME}' on '{STREAM_NAME}'")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            print(f"[processing] consumer group '{GROUP_NAME}' already exists")
        else:
            raise

    conn = await asyncpg.connect(DB_URL)
    await init_db(conn)
    print(f"[processing] connected to TimescaleDB at {DB_URL}")
    print(f"[processing] listening on '{STREAM_NAME}' ...")

    while True:
        results = await r.xreadgroup(
            GROUP_NAME, CONSUMER_ID,
            {STREAM_NAME: ">"},
            block=BLOCK_MS, count=20,
        )
        if not results:
            continue

        rows: list[tuple] = []
        ack_ids: list[str] = []

        for _stream, messages in results:
            for msg_id, fields in messages:
                try:
                    payload = json.loads(fields.get("payload", "{}"))
                    row     = process(payload)
                    rows.append((
                        row["time"],
                        row["loco_id"],
                        row["health_score"],
                        row["health_category"],
                        row["alert_count"],
                        row["params"],
                    ))
                    ack_ids.append(msg_id)
                    print(
                        f"[processing] loco={row['loco_id']} "
                        f"score={row['health_score']} "
                        f"cat={row['health_category']} "
                        f"alerts={row['alert_count']}"
                    )
                except Exception as exc:
                    print(f"[processing] ERROR msg={msg_id}: {exc}")

        if rows:
            try:
                await conn.executemany(INSERT_SQL, rows)
                for msg_id in ack_ids:
                    await r.xack(STREAM_NAME, GROUP_NAME, msg_id)
            except Exception as exc:
                print(f"[processing] DB write error: {exc}")

if __name__ == "__main__":
    asyncio.run(main())
