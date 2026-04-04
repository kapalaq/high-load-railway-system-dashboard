import asyncio
import json
import logging
import math
import os
import random
import time

import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [simulator] %(message)s")
logger = logging.getLogger(__name__)

INGESTION_URL = os.getenv("INGESTION_URL", "ws://localhost:8001/ws/telemetry")
HZ = float(os.getenv("HZ", "10"))
RECONNECT_DELAY_S = float(os.getenv("RECONNECT_DELAY_S", "2"))

# ---------------------------------------------------------------------------
# Locomotive definitions
# Each dict: train_id, loco_type, locomotive_type, route, total_km, phase_offset
# phase_offset spreads the sine waves so each loco looks different on the dashboard
# ---------------------------------------------------------------------------

ROUTES = {
    "AKA": {
        "route_name": "Astana - Karaganda - Almaty",
        "total_distance_km": 1211,
        "stops": [
            {"name": "Astana",    "distance_km": 0,    "status": "passed"},
            {"name": "Karaganda", "distance_km": 211,  "status": "upcoming"},
            {"name": "Almaty",    "distance_km": 1211, "status": "upcoming"},
        ],
    },
    "AKA_REV": {
        "route_name": "Almaty - Karaganda - Astana",
        "total_distance_km": 1211,
        "stops": [
            {"name": "Almaty",    "distance_km": 0,    "status": "passed"},
            {"name": "Karaganda", "distance_km": 1000, "status": "upcoming"},
            {"name": "Astana",    "distance_km": 1211, "status": "upcoming"},
        ],
    },
}

LOCOS = [
    # 5x KZ8A (Electric)
    {"train_id": "KZ8A-L001", "loco_type": "KZ8A", "locomotive_type": "Electric", "route_key": "AKA",     "phase_offset": 0.0},
    {"train_id": "KZ8A-L002", "loco_type": "KZ8A", "locomotive_type": "Electric", "route_key": "AKA",     "phase_offset": 0.7},
    {"train_id": "KZ8A-L003", "loco_type": "KZ8A", "locomotive_type": "Electric", "route_key": "AKA_REV", "phase_offset": 1.3},
    {"train_id": "KZ8A-L004", "loco_type": "KZ8A", "locomotive_type": "Electric", "route_key": "AKA",     "phase_offset": 2.1},
    {"train_id": "KZ8A-L005", "loco_type": "KZ8A", "locomotive_type": "Electric", "route_key": "AKA_REV", "phase_offset": 2.8},
    # 5x TE33A (Diesel)
    {"train_id": "TE33A-L006", "loco_type": "TE33A", "locomotive_type": "Diesel", "route_key": "AKA",     "phase_offset": 0.3},
    {"train_id": "TE33A-L007", "loco_type": "TE33A", "locomotive_type": "Diesel", "route_key": "AKA_REV", "phase_offset": 1.0},
    {"train_id": "TE33A-L008", "loco_type": "TE33A", "locomotive_type": "Diesel", "route_key": "AKA",     "phase_offset": 1.7},
    {"train_id": "TE33A-L009", "loco_type": "TE33A", "locomotive_type": "Diesel", "route_key": "AKA_REV", "phase_offset": 2.4},
    {"train_id": "TE33A-L010", "loco_type": "TE33A", "locomotive_type": "Diesel", "route_key": "AKA",     "phase_offset": 3.1},
]


# ---------------------------------------------------------------------------
# Telemetry generation helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _compute_stops(position_km: float, stops: list[dict]) -> list[dict]:
    result = []
    for stop in stops:
        d = stop["distance_km"]
        if position_km >= d + 5:
            status = "passed"
        elif abs(position_km - d) <= 5:
            status = "current"
        else:
            status = "upcoming"
        result.append({"name": stop["name"], "distance_km": d, "status": status})
    return result


def _build_metrics_kz8a(t: float, ph: float) -> list[dict]:
    speed = 80 + 25 * math.sin(t / 60 + ph) + random.gauss(0, 1.5)
    speed = round(max(0.0, min(speed, 200.0)), 2)

    motor_temp = 110 + 20 * math.sin(t / 45 + ph + 1.0) + random.gauss(0, 2)
    motor_temp = round(max(20.0, min(motor_temp, 200.0)), 2)

    pantograph_v = round(25.0 + random.gauss(0, 0.3), 2)

    brake_p = round(5.0 + 0.3 * math.sin(t / 30 + ph) + random.gauss(0, 0.1), 2)

    return [
        {"key": "speed",              "name_ru": "Скорость",                      "unit": "km/h", "current_value": speed},
        {"key": "motor_temp_1",       "name_ru": "Температура ТЭД #1",            "unit": "°C",   "current_value": motor_temp},
        {"key": "pantograph_voltage", "name_ru": "Напряжение контактной сети",    "unit": "kV",   "current_value": pantograph_v},
        {"key": "brake_pressure_1",   "name_ru": "Давление тормозной магистрали", "unit": "bar",  "current_value": brake_p},
    ]


def _build_metrics_te33a(t: float, ph: float) -> list[dict]:
    speed = 60 + 20 * math.sin(t / 70 + ph) + random.gauss(0, 1.5)
    speed = round(max(0.0, min(speed, 200.0)), 2)

    oil_pressure = 5.0 + 1.5 * math.sin(t / 50 + ph) + random.gauss(0, 0.2)
    oil_pressure = round(max(0.0, min(oil_pressure, 10.0)), 2)

    # Fuel level drains slowly over time, resets at 5%
    fuel_level = round(max(5.0, 80.0 - (t % 7200) / 90.0 + random.gauss(0, 0.5)), 1)

    engine_temp = round(88 + 12 * math.sin(t / 55 + ph) + random.gauss(0, 2), 2)

    return [
        {"key": "speed",               "name_ru": "Скорость",        "unit": "km/h", "current_value": speed},
        {"key": "engine_oil_pressure", "name_ru": "Давление масла",  "unit": "bar",  "current_value": oil_pressure},
        {"key": "fuel_level",          "name_ru": "Уровень топлива", "unit": "%",    "current_value": fuel_level},
        {"key": "engine_temp",         "name_ru": "Температура ДГУ", "unit": "°C",   "current_value": engine_temp},
    ]


def generate_telemetry(loco: dict, t: float) -> dict:
    ph = loco["phase_offset"]
    route = ROUTES[loco["route_key"]]

    # Position advances proportionally to time, wraps around total route
    avg_speed_km_s = 90.0 / 3600.0  # ~90 km/h average in km/s
    position_km = round((avg_speed_km_s * t + ph * 50) % route["total_distance_km"], 2)

    if loco["loco_type"] == "KZ8A":
        metrics = _build_metrics_kz8a(t, ph)
    else:
        metrics = _build_metrics_te33a(t, ph)

    return {
        "train_id": loco["train_id"],
        "locomotive_type": loco["locomotive_type"],
        "timestamp": _iso_now(),
        "route_info": {
            "route_name": route["route_name"],
            "total_distance_km": route["total_distance_km"],
            "current_position_km": position_km,
            "stops": _compute_stops(position_km, route["stops"]),
        },
        "telemetry_config": {
            "metrics": metrics,
        },
    }


# ---------------------------------------------------------------------------
# WebSocket coroutine per locomotive
# ---------------------------------------------------------------------------

async def run_loco(loco: dict) -> None:
    train_id = loco["train_id"]
    sleep_s = 1.0 / HZ

    while True:  # outer reconnect loop
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
