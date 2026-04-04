import asyncio
import json
import logging
import time

import websockets

from config import INGESTION_URL, HZ, RECONNECT_DELAY_S, LOCOS, QUERY_API_WS_URL, QUERY_API_TOKEN
from generators import generate_telemetry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [simulator] %(message)s")
logger = logging.getLogger(__name__)

# Tracks monotonic send time of the most recent telemetry message per train.
_last_send: dict[str, float] = {}

# Per-train validation counters: {train_id: {"matched": int, "foreign": int, "invalid_json": int}}
_validation: dict[str, dict[str, int]] = {}

VALIDATION_REPORT_INTERVAL_S = 5


async def run_loco(loco: dict) -> None:
    train_id = loco["train_id"]
    sleep_s = 1.0 / HZ

    while True:
        try:
            async with websockets.connect(INGESTION_URL) as ws:
                logger.info("[%s] connected to %s", train_id, INGESTION_URL)
                while True:
                    deadline = time.monotonic() + sleep_s
                    payload = generate_telemetry(loco, time.time())
                    _last_send[train_id] = time.monotonic()
                    await ws.send(json.dumps(payload))
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        await asyncio.sleep(remaining)
        except (websockets.ConnectionClosed, websockets.InvalidHandshake, OSError) as e:
            logger.warning("[%s] disconnected (%s), retrying in %.1fs", train_id, e, RECONNECT_DELAY_S)
            await asyncio.sleep(RECONNECT_DELAY_S)
        except Exception as e:
            logger.error("[%s] unexpected error: %s, retrying in %.1fs", train_id, e, RECONNECT_DELAY_S)
            await asyncio.sleep(RECONNECT_DELAY_S)


async def run_rtt_monitor(loco: dict) -> None:
    """Connect to query-api WebSocket, measure RTT, and validate no foreign data leaks in."""
    train_id = loco["train_id"]
    url = f"{QUERY_API_WS_URL}?token={QUERY_API_TOKEN}&train_id={train_id}"
    _validation[train_id] = {"matched": 0, "foreign": 0, "invalid_json": 0}

    while True:
        try:
            async with websockets.connect(url) as ws:
                logger.info("[%s] RTT monitor connected to query-api", train_id)
                async for raw in ws:
                    recv_time = time.monotonic()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        _validation[train_id]["invalid_json"] += 1
                        logger.warning("[%s] VALIDATION: received invalid JSON", train_id)
                        continue

                    received_id = data.get("train_id")
                    if received_id != train_id:
                        _validation[train_id]["foreign"] += 1
                        logger.error(
                            "[%s] VALIDATION FAIL: foreign data leak — expected %s, got %s",
                            train_id, train_id, received_id,
                        )
                        continue

                    _validation[train_id]["matched"] += 1
                    send_time = _last_send.get(train_id)
                    if send_time is not None:
                        rtt_ms = (recv_time - send_time) * 1000
                        logger.info("[%s] RTT=%.1f ms", train_id, rtt_ms)
        except (websockets.ConnectionClosed, websockets.InvalidHandshake, OSError) as e:
            logger.warning("[%s] RTT monitor disconnected (%s), retrying in %.1fs", train_id, e, RECONNECT_DELAY_S)
            await asyncio.sleep(RECONNECT_DELAY_S)
        except Exception as e:
            logger.error("[%s] RTT monitor error: %s, retrying in %.1fs", train_id, e, RECONNECT_DELAY_S)
            await asyncio.sleep(RECONNECT_DELAY_S)


async def run_validation_reporter() -> None:
    """Periodically print a per-train isolation summary."""
    await asyncio.sleep(VALIDATION_REPORT_INTERVAL_S)
    while True:
        logger.info("=== ISOLATION VALIDATION REPORT ===")
        all_pass = True
        for train_id, stats in sorted(_validation.items()):
            foreign = stats["foreign"]
            invalid = stats["invalid_json"]
            matched = stats["matched"]
            status = "PASS" if foreign == 0 and invalid == 0 else "FAIL"
            if status == "FAIL":
                all_pass = False
            logger.info(
                "  [%s] %s — matched=%d  foreign=%d  invalid_json=%d",
                train_id, status, matched, foreign, invalid,
            )
        logger.info("  OVERALL: %s", "ALL PASS" if all_pass else "FAILURES DETECTED")
        logger.info("===================================")
        await asyncio.sleep(VALIDATION_REPORT_INTERVAL_S)


async def main() -> None:
    logger.info("Starting %d locomotive simulators at %.0f Hz → %s", len(LOCOS), HZ, INGESTION_URL)
    tasks = (
        [run_loco(loco) for loco in LOCOS]
        + [run_rtt_monitor(loco) for loco in LOCOS]
        # + [run_validation_reporter()]
    )
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
