import asyncio
import json
import logging
import time

import websockets

from config import INGESTION_URL, HZ, RECONNECT_DELAY_S, LOCOS
from generators import generate_telemetry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [simulator] %(message)s")
logger = logging.getLogger(__name__)


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


async def main() -> None:
    logger.info("Starting %d locomotive simulators at %.0f Hz → %s", len(LOCOS), HZ, INGESTION_URL)
    await asyncio.gather(*[run_loco(loco) for loco in LOCOS])


if __name__ == "__main__":
    asyncio.run(main())
