import os
import json
import asyncio
import redis.asyncio as aio_redis

from db import get_connection, init_db, INSERT_SQL
from processing import process

REDIS_URL   = os.environ.get("REDIS_URL",   "redis://localhost:6379")
STREAM_NAME = os.environ.get("STREAM_NAME", "telemetry:raw")
GROUP_NAME  = "processing-group"
CONSUMER_ID = "processor-1"
BLOCK_MS    = 2000


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

    conn = await get_connection()
    await init_db(conn)
    print(f"[processing] listening on '{STREAM_NAME}' ...")

    while True:
        results = await r.xreadgroup(
            GROUP_NAME, CONSUMER_ID,
            {STREAM_NAME: ">"},
            block=BLOCK_MS, count=20,
        )
        if not results:
            continue

        rows:    list[tuple] = []
        ack_ids: list[str]   = []

        for _stream, messages in results:
            for msg_id, fields in messages:
                try:
                    payload = json.loads(fields.get("payload", "{}"))
                    row     = process(payload)
                    rows.append(row.db_tuple())
                    ack_ids.append(msg_id)
                    print(
                        f"[processing] loco={row.loco_id} "
                        f"score={row.health_score} "
                        f"cat={row.health_category} "
                        f"alerts={row.alert_count}"
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
