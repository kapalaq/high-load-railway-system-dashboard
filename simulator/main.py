import asyncio
import collections
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

from config import (
    INGESTION_URL, HZ, RECONNECT_DELAY_S, LOCOS, QUERY_API_WS_URL, QUERY_API_TOKEN,
    OFFLINE_HZ, BUFFER_CAP, BUFFER_DIR, REPLAY_HZ,
)
from generators import generate_telemetry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [simulator] %(message)s")
logger = logging.getLogger(__name__)

# Tracks monotonic send time of the most recent telemetry message per train.
_last_send: dict[str, float] = {}

# Per-train isolation counters: {train_id: {"matched": int, "foreign": int, "invalid_json": int}}
_validation: dict[str, dict[str, int]] = {}

# Per-train schema stats: ws_checked/ws_failures track every WS message;
# http_checked/http_failures/parity_failures are updated each reporter cycle.
_schema_stats: dict[str, dict] = {
    loco["train_id"]: {
        "ws_checked": 0, "ws_failures": 0,
        "http_checked": 0, "http_failures": 0,
        "parity_failures": 0, "last_errors": [],
    }
    for loco in LOCOS
}

# Most recent structurally-valid WS message per train (for HTTP parity checks).
_last_ws_msg: dict[str, dict] = {}

VALIDATION_REPORT_INTERVAL_S = 5
SCHEMA_REPORT_INTERVAL_S = 15

# ── schema constants ──────────────────────────────────────────────────────────

# Derive HTTP base from the WS URL: ws://host:port/api/... → http://host:port
_HTTP_BASE = QUERY_API_WS_URL.replace("ws://", "http://").split("/api/")[0]

_REQUIRED_TOP_LEVEL = {
    "train_id", "health_score", "health_category", "alert_count",
    "top_impacts", "params", "route_info", "time",
}
_REQUIRED_METRIC_KEYS = {
    "name", "value", "unit", "status", "range", "range_label",
    "alert_message", "recommendation", "min", "max", "norm_min", "norm_max",
}
_REQUIRED_IMPACT_KEYS = {"metric", "status", "impact"}
_REQUIRED_ROUTE_KEYS = {
    "route_name", "total_distance_km", "current_position_km",
    "current", "stops", "distance_left_km", "time_left_h", "info",
}
_REQUIRED_ROUTE_INFO_KEYS = {
    "distance_left_km", "time_left_h", "name", "status", "recommendation",
}


# ── schema helpers ────────────────────────────────────────────────────────────

def _check_schema(data: dict, source: str) -> list[str]:
    """Return a list of schema error strings (empty list means valid)."""
    errors: list[str] = []

    missing = _REQUIRED_TOP_LEVEL - data.keys()
    if missing:
        errors.append(f"missing top-level keys: {missing}")
        return errors  # remaining checks depend on these keys

    if not isinstance(data["health_score"], (int, float)):
        errors.append(f"health_score not numeric: {type(data['health_score'])}")
    elif not (0.0 <= data["health_score"] <= 100.0):
        errors.append(f"health_score out of range: {data['health_score']}")

    if not isinstance(data["alert_count"], int) or data["alert_count"] < 0:
        errors.append(f"alert_count invalid: {data['alert_count']}")

    for i, entry in enumerate(data.get("top_impacts") or []):
        miss = _REQUIRED_IMPACT_KEYS - entry.keys()
        if miss:
            errors.append(f"top_impacts[{i}] missing keys: {miss}")

    for key, metric in (data.get("params") or {}).items():
        if key == "system_condition":
            if not isinstance(metric.get("value"), list):
                errors.append("params.system_condition.value is not a list")
            continue
        miss = _REQUIRED_METRIC_KEYS - metric.keys()
        if miss:
            errors.append(f"params[{key!r}] missing keys: {miss}")

    ri = data.get("route_info") or {}
    miss = _REQUIRED_ROUTE_KEYS - ri.keys()
    if miss:
        errors.append(f"route_info missing keys: {miss}")
    else:
        miss = _REQUIRED_ROUTE_INFO_KEYS - (ri.get("info") or {}).keys()
        if miss:
            errors.append(f"route_info.info missing keys: {miss}")

    return errors


def _check_parity(ws: dict, http: dict) -> list[str]:
    """Return errors where WS and HTTP structures diverge."""
    errors: list[str] = []

    ws_keys, http_keys = set(ws), set(http)
    if ws_keys != http_keys:
        errors.append(
            f"top-level mismatch — WS only: {ws_keys - http_keys}, "
            f"HTTP only: {http_keys - ws_keys}"
        )

    ws_params = set(ws.get("params") or {})
    http_params = set(http.get("params") or {})
    if ws_params != http_params:
        errors.append(
            f"params key mismatch — WS only: {ws_params - http_params}, "
            f"HTTP only: {http_params - ws_params}"
        )
    else:
        for key in ws_params & http_params:
            if key == "system_condition":
                continue
            wk = set((ws["params"] or {}).get(key, {}))
            hk = set((http["params"] or {}).get(key, {}))
            if wk != hk:
                errors.append(
                    f"params[{key!r}] key mismatch — "
                    f"WS only: {wk - hk}, HTTP only: {hk - wk}"
                )

    ws_ri = set(ws.get("route_info") or {})
    http_ri = set(http.get("route_info") or {})
    if ws_ri != http_ri:
        errors.append(
            f"route_info mismatch — WS only: {ws_ri - http_ri}, "
            f"HTTP only: {http_ri - ws_ri}"
        )

    return errors


def _fetch_http_record_sync(train_id: str, position_km: float) -> dict | None:
    """Blocking HTTP call; run via executor to avoid blocking the event loop."""
    url = f"{_HTTP_BASE}/api/historic/telemetry/{train_id}?distance={position_km}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {QUERY_API_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("[schema] HTTP %d for %s", e.code, train_id)
    except Exception as e:
        logger.warning("[schema] HTTP fetch failed for %s: %s", train_id, e)
    return None


# ── coroutines ────────────────────────────────────────────────────────────────

class OfflineBuffer:
    """
    Ring-buffered JSONL file for one locomotive.

    Buffers telemetry payloads to disk when the simulator is offline.
    Hydrates from disk on startup so data survives process crashes.
    iter_and_drain() removes each entry only after the caller resumes
    (i.e. after a successful ws.send), so replay interruptions leave
    the remaining tail intact for the next reconnect.
    """

    def __init__(self, train_id: str, cap: int, buf_dir: str) -> None:
        self._path = Path(buf_dir) / f"{train_id}.jsonl"
        Path(buf_dir).mkdir(parents=True, exist_ok=True)
        self._ring: collections.deque = collections.deque(maxlen=cap)
        if self._path.exists():
            with self._path.open() as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            self._ring.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            logger.info("[%s] loaded %d buffered entries from disk", train_id, len(self._ring))

    def push(self, payload: dict) -> None:
        self._ring.append(payload)
        self._flush_to_disk()

    def __len__(self) -> int:
        return len(self._ring)

    def is_empty(self) -> bool:
        return not self._ring

    def _flush_to_disk(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w") as fh:
            for entry in self._ring:
                fh.write(json.dumps(entry) + "\n")
        os.replace(tmp, self._path)  # atomic rename on POSIX

    def iter_and_drain(self):
        """
        Generator: yields each buffered entry as a raw JSON string.
        Pops the entry from the ring only after the caller resumes the
        generator (i.e. after successful ws.send). If the generator is
        abandoned mid-way, remaining entries stay in the ring.
        Deletes the disk file when fully drained.
        """
        while self._ring:
            yield json.dumps(self._ring[0])
            self._ring.popleft()
        self._path.unlink(missing_ok=True)


async def replay_buffer_in_background(train_id: str, buffer: OfflineBuffer) -> None:
    """
    Opens a dedicated WebSocket connection to ingestion and slowly drains
    the offline buffer at REPLAY_HZ. Runs concurrently with the live data
    task — has zero impact on live telemetry throughput.
    If the connection drops mid-replay, remaining entries stay in the buffer
    and will be retried on the next reconnect cycle.
    """
    replay_sleep = 1.0 / REPLAY_HZ
    count = len(buffer)
    logger.info("[%s] background replay starting: %d entries at %.0f Hz", train_id, count, REPLAY_HZ)
    try:
        async with websockets.connect(INGESTION_URL) as ws:
            for line in buffer.iter_and_drain():
                # Tag as replay so processing service skips Pub/Sub (frontend won't see it)
                payload = json.loads(line)
                payload["_replay"] = True
                await ws.send(json.dumps(payload))
                await asyncio.sleep(replay_sleep)
        logger.info("[%s] background replay complete", train_id)
    except Exception as e:
        logger.warning(
            "[%s] background replay interrupted (%s) — will retry on next reconnect", train_id, e
        )


async def run_loco(loco: dict) -> None:
    train_id      = loco["train_id"]
    live_sleep    = 1.0 / HZ
    offline_sleep = 1.0 / OFFLINE_HZ

    buffer = OfflineBuffer(train_id, cap=BUFFER_CAP, buf_dir=BUFFER_DIR)

    while True:
        # ── CONNECT ──────────────────────────────────────────────────────
        try:
            async with websockets.connect(INGESTION_URL) as ws:
                logger.info("[%s] connected to %s", train_id, INGESTION_URL)

                # Kick off background replay on a separate connection (non-blocking)
                if not buffer.is_empty():
                    asyncio.create_task(replay_buffer_in_background(train_id, buffer))

                # Live telemetry starts immediately — unaffected by replay
                while True:
                    deadline = time.monotonic() + live_sleep
                    payload = generate_telemetry(loco, time.time())
                    _last_send[train_id] = time.monotonic()
                    await ws.send(json.dumps(payload))
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        await asyncio.sleep(remaining)

        except (websockets.ConnectionClosed, websockets.InvalidHandshake, OSError) as e:
            logger.warning("[%s] disconnected (%s) — buffering at %.0f Hz", train_id, e, OFFLINE_HZ)
        except Exception as e:
            logger.error("[%s] unexpected error: %s — buffering at %.0f Hz", train_id, e, OFFLINE_HZ)

        # ── OFFLINE: sample at reduced rate until reconnect window expires ──
        deadline = time.monotonic() + RECONNECT_DELAY_S
        while time.monotonic() < deadline:
            payload = generate_telemetry(loco, time.time())
            buffer.push(payload)
            await asyncio.sleep(offline_sleep)

        logger.info("[%s] buffer size=%d, retrying connection", train_id, len(buffer))


async def run_rtt_monitor(loco: dict) -> None:
    """Connect to query-api WebSocket, measure RTT, validate isolation and message schema."""
    train_id = loco["train_id"]
    url = f"{QUERY_API_WS_URL}?token={QUERY_API_TOKEN}&train_id={train_id}"
    _validation[train_id] = {"matched": 0, "foreign": 0, "invalid_json": 0}

    while True:
        try:
            async with websockets.connect(url) as ws:
                # logger.info("[%s] RTT monitor connected to query-api", train_id)
                async for raw in ws:
                    recv_time = time.monotonic()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        _validation[train_id]["invalid_json"] += 1
                        # logger.warning("[%s] VALIDATION: received invalid JSON", train_id)
                        continue

                    received_id = data.get("train_id")
                    if received_id != train_id:
                        _validation[train_id]["foreign"] += 1
                        # logger.error(
                        #     "[%s] VALIDATION FAIL: foreign data leak — expected %s, got %s",
                        #     train_id, train_id, received_id,
                        # )
                        continue

                    _validation[train_id]["matched"] += 1
                    send_time = _last_send.get(train_id)
                    if send_time is not None:
                        rtt_ms = (recv_time - send_time) * 1000
                        # logger.info("[%s] RTT=%.1f ms", train_id, rtt_ms)

                    # Schema validation on every received WS message
                    stats = _schema_stats[train_id]
                    stats["ws_checked"] += 1
                    errors = _check_schema(data, f"WS/{train_id}")
                    if errors:
                        stats["ws_failures"] += 1
                        stats["last_errors"] = [f"WS: {e}" for e in errors]
                    else:
                        _last_ws_msg[train_id] = data

        except (websockets.ConnectionClosed, websockets.InvalidHandshake, OSError) as e:
            # logger.warning("[%s] RTT monitor disconnected (%s), retrying in %.1fs", train_id, e, RECONNECT_DELAY_S)
            await asyncio.sleep(RECONNECT_DELAY_S)
        except Exception as e:
            # logger.error("[%s] RTT monitor error: %s, retrying in %.1fs", train_id, e, RECONNECT_DELAY_S)
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


async def run_schema_reporter() -> None:
    """Periodically validate WS and HTTP response schemas and check their parity."""
    await asyncio.sleep(SCHEMA_REPORT_INTERVAL_S)
    loop = asyncio.get_event_loop()

    while True:
        logger.info("=== SCHEMA VALIDATION REPORT ===")
        overall_pass = True

        for loco in LOCOS:
            train_id = loco["train_id"]
            stats = _schema_stats[train_id]
            ws_msg = _last_ws_msg.get(train_id)

            # WS schema result (accumulated across all received messages)
            if stats["ws_checked"] == 0:
                ws_label = "WAIT"
                overall_pass = False
            else:
                ws_label = "PASS" if stats["ws_failures"] == 0 else "FAIL"
                if ws_label != "PASS":
                    overall_pass = False

            logger.info(
                "  [%s] WS     %s — checked=%-4d  failures=%d",
                train_id, ws_label, stats["ws_checked"], stats["ws_failures"],
            )
            if ws_label == "FAIL" and stats["last_errors"]:
                for err in stats["last_errors"]:
                    logger.info("    ! %s", err)

            # HTTP schema + parity (checked once per reporter cycle)
            if ws_msg is None:
                logger.info("  [%s] HTTP   WAIT — no WS message yet", train_id)
                logger.info("  [%s] PARITY WAIT", train_id)
                overall_pass = False
                continue

            position_km = (ws_msg.get("route_info") or {}).get("current_position_km", 300.0)
            http_record = await loop.run_in_executor(
                None, _fetch_http_record_sync, train_id, position_km
            )
            stats["http_checked"] += 1

            if http_record is None:
                stats["http_failures"] += 1
                overall_pass = False
                logger.info(
                    "  [%s] HTTP   FAIL — checked=%-4d  failures=%d  (endpoint unreachable)",
                    train_id, stats["http_checked"], stats["http_failures"],
                )
                continue

            http_errors = _check_schema(http_record, f"HTTP/{train_id}")
            if http_errors:
                stats["http_failures"] += 1
                overall_pass = False
                logger.info(
                    "  [%s] HTTP   FAIL — checked=%-4d  failures=%d",
                    train_id, stats["http_checked"], stats["http_failures"],
                )
                for err in http_errors:
                    logger.info("    ! HTTP: %s", err)
            else:
                logger.info(
                    "  [%s] HTTP   PASS — checked=%-4d  failures=%d",
                    train_id, stats["http_checked"], stats["http_failures"],
                )

            parity_errors = _check_parity(ws_msg, http_record)
            if parity_errors:
                stats["parity_failures"] += 1
                overall_pass = False
                logger.info(
                    "  [%s] PARITY FAIL — failures=%d",
                    train_id, stats["parity_failures"],
                )
                for err in parity_errors:
                    logger.info("    ! %s", err)
            else:
                logger.info(
                    "  [%s] PARITY PASS — failures=%d",
                    train_id, stats["parity_failures"],
                )

        logger.info("  OVERALL: %s", "ALL PASS" if overall_pass else "FAILURES DETECTED")
        logger.info("=================================")
        await asyncio.sleep(SCHEMA_REPORT_INTERVAL_S)


async def main() -> None:
    logger.info("Starting %d locomotive simulators at %.0f Hz → %s", len(LOCOS), HZ, INGESTION_URL)
    tasks = (
        [run_loco(loco) for loco in LOCOS]
        + [run_rtt_monitor(loco) for loco in LOCOS]
        + [run_validation_reporter()]
        + [run_schema_reporter()]
    )
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
