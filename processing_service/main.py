import os
import json
import asyncio
import logging
from dataclasses import dataclass

import redis.asyncio as aio_redis
import asyncpg

from db import INSERT_SQL, init_db
from processing import process

logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
log = logging.getLogger("processing")

STREAM_NAME    = os.environ.get("STREAM_NAME", "telemetry:raw")
GROUP_NAME     = "processing-group"
CONSUMER_ID    = "processor-1"
BLOCK_MS       = 2000
PUBSUB_CHANNEL = "metrics:live"


@dataclass
class AppConfig:
    redis_url:         str = os.environ.get("REDIS_URL", "redis://localhost:6379")
    db_url:            str = os.environ.get("DB_URL", "postgresql://user:password@localhost:5432/locomotive")
    batch_size:        int = 200
    flush_interval_ms: int = 500
    queue_maxsize:     int = 10_000
    pg_pool_min:       int = 2
    pg_pool_max:       int = 5


async def flush_batch(batch: list, pg_pool: asyncpg.Pool, retries: int = 3) -> None:
    for attempt in range(retries):
        try:
            async with pg_pool.acquire() as conn:
                await conn.executemany(INSERT_SQL, batch)
            log.info(f"{len(batch)} row(s) inserted into TimescaleDB.")
            return
        except Exception as e:
            if attempt == retries - 1:
                log.error(f"Batch flush failed after {retries} attempts: {e}")
                return
            await asyncio.sleep(0.1 * (2 ** attempt))


async def db_worker(queue: asyncio.Queue, pg_pool: asyncpg.Pool, cfg: AppConfig) -> None:
    batch: list = []
    interval = cfg.flush_interval_ms / 1000
    flush_deadline = asyncio.get_event_loop().time() + interval

    while True:
        timeout = flush_deadline - asyncio.get_event_loop().time()
        if timeout > 0:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=timeout)
                batch.append(item)
                queue.task_done()
            except asyncio.TimeoutError:
                pass

        now = asyncio.get_event_loop().time()
        if now >= flush_deadline:
            if batch:
                await flush_batch(batch, pg_pool)
                batch.clear()
            flush_deadline = now + interval
        elif len(batch) >= cfg.batch_size:
            await flush_batch(batch, pg_pool)
            batch.clear()
            flush_deadline = asyncio.get_event_loop().time() + interval


async def ingest_loop(r: aio_redis.Redis, queue: asyncio.Queue) -> None:
    try:
        await r.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
        log.info(f"created consumer group '{GROUP_NAME}' on '{STREAM_NAME}'")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            log.info(f"consumer group '{GROUP_NAME}' already exists")
        else:
            raise

    log.info(f"listening on '{STREAM_NAME}' ...")

    while True:
        results = await r.xreadgroup(
            GROUP_NAME, CONSUMER_ID,
            {STREAM_NAME: ">"},
            block=BLOCK_MS, count=20,
        )
        if not results:
            continue

        ack_ids: list[str] = []

        for _stream, messages in results:
            for msg_id, fields in messages:
                try:
                    payload = json.loads(fields.get("payload", "{}"))
                    row     = process(payload)

                    # Publish summary immediately — don't block on DB
                    await r.publish(PUBSUB_CHANNEL, json.dumps({
                        "train_id":        row.train_id,
                        "health_score":    row.health_score,
                        "health_category": row.health_category,
                        "alert_count":     row.alert_count,
                        "route_info":      row.route_info,
                        "params":          row.params,
                        "time":            row.time.isoformat(),
                    }))

                    # Hand off to DB worker — non-blocking
                    try:
                        queue.put_nowait(row.db_tuple())
                    except asyncio.QueueFull:
                        log.warning("Queue full — applying backpressure")
                        await queue.put(row.db_tuple())

                    ack_ids.append(msg_id)
                except Exception as exc:
                    log.error(f"ERROR msg={msg_id}: {exc}", exc_info=True)

        for msg_id in ack_ids:
            await r.xack(STREAM_NAME, GROUP_NAME, msg_id)
        log.info(f"{len(ack_ids)} message(s) published to Redis Pub/Sub.")


async def main() -> None:
    cfg = AppConfig()

    log.info(f"Connecting to Redis at {cfg.redis_url} ...")
    r = aio_redis.from_url(cfg.redis_url, decode_responses=True)
    await r.ping()
    log.info("Redis OK")

    log.info(f"Connecting to TimescaleDB at {cfg.db_url} ...")
    pg_pool = await asyncpg.create_pool(
        cfg.db_url, min_size=cfg.pg_pool_min, max_size=cfg.pg_pool_max
    )
    async with pg_pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
        await init_db(conn)
    log.info("TimescaleDB OK")

    queue: asyncio.Queue = asyncio.Queue(maxsize=cfg.queue_maxsize)

    worker   = asyncio.create_task(db_worker(queue, pg_pool, cfg))
    ingester = asyncio.create_task(ingest_loop(r, queue))

    try:
        await asyncio.gather(worker, ingester)
    except asyncio.CancelledError:
        ingester.cancel()
        await queue.join()   # drain before exit
        worker.cancel()
        await asyncio.gather(worker, ingester, return_exceptions=True)
    except Exception as e:
        log.error(f"Fatal error in tasks: {e}", exc_info=True)
        worker.cancel()
        ingester.cancel()
        await asyncio.gather(worker, ingester, return_exceptions=True)
        raise
    finally:
        await pg_pool.close()
        await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
