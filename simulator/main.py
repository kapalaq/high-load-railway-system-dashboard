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
# phase_offset spreads sine waves so each loco looks different on the dashboard
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
    {"train_id": "KZ8A-L001", "loco_type": "KZ8A",  "locomotive_type": "Electric", "route_key": "AKA",     "phase_offset": 0.0},
    {"train_id": "KZ8A-L002", "loco_type": "KZ8A",  "locomotive_type": "Electric", "route_key": "AKA",     "phase_offset": 0.7},
    {"train_id": "KZ8A-L003", "loco_type": "KZ8A",  "locomotive_type": "Electric", "route_key": "AKA_REV", "phase_offset": 1.3},
    {"train_id": "KZ8A-L004", "loco_type": "KZ8A",  "locomotive_type": "Electric", "route_key": "AKA",     "phase_offset": 2.1},
    {"train_id": "KZ8A-L005", "loco_type": "KZ8A",  "locomotive_type": "Electric", "route_key": "AKA_REV", "phase_offset": 2.8},
    # 5x TE33A (Diesel)
    {"train_id": "TE33A-L006", "loco_type": "TE33A", "locomotive_type": "Diesel",  "route_key": "AKA",     "phase_offset": 0.3},
    {"train_id": "TE33A-L007", "loco_type": "TE33A", "locomotive_type": "Diesel",  "route_key": "AKA_REV", "phase_offset": 1.0},
    {"train_id": "TE33A-L008", "loco_type": "TE33A", "locomotive_type": "Diesel",  "route_key": "AKA",     "phase_offset": 1.7},
    {"train_id": "TE33A-L009", "loco_type": "TE33A", "locomotive_type": "Diesel",  "route_key": "AKA_REV", "phase_offset": 2.4},
    {"train_id": "TE33A-L010", "loco_type": "TE33A", "locomotive_type": "Diesel",  "route_key": "AKA",     "phase_offset": 3.1},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


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


# ---------------------------------------------------------------------------
# Shared metric generators (same for both loco types)
# ---------------------------------------------------------------------------

def _gen_temp_oil(t: float, ph: float) -> float:
    """Oil temp: normally 55-75°C, occasionally drifts into warning (>80°C)."""
    return round(_clamp(65 + 15 * math.sin(t / 50 + ph + 0.5) + random.gauss(0, 1.5), 40, 150), 1)


def _gen_temp_converters(t: float, ph: float) -> float:
    """Converter temp: normally 35-55°C, well within normal (<120°C)."""
    return round(_clamp(45 + 10 * math.sin(t / 80 + ph + 1.2) + random.gauss(0, 1), 20, 200), 1)


def _gen_temp_air(t: float, ph: float) -> float:
    """Air temp inside: normally 18-30°C."""
    return round(_clamp(24 + 6 * math.sin(t / 120 + ph) + random.gauss(0, 0.5), -20, 200), 1)


def _gen_pressure_main_tank(t: float, ph: float) -> float:
    """Main reservoir: normal 6-9 bar, occasional dip toward warning."""
    return round(_clamp(7.5 + 1.0 * math.sin(t / 40 + ph + 2.0) + random.gauss(0, 0.2), 0, 10), 2)


def _gen_pressure_brake(t: float, ph: float) -> float:
    """Brake pipeline: normal 5-8 bar."""
    return round(_clamp(6.5 + 1.2 * math.sin(t / 35 + ph + 0.8) + random.gauss(0, 0.15), 0, 10), 2)


def _gen_pressure_air(t: float, ph: float) -> float:
    """Air pressure: normal 0.6-1.6 bar."""
    return round(_clamp(1.1 + 0.3 * math.sin(t / 25 + ph + 1.5) + random.gauss(0, 0.05), 0, 5), 2)


def _gen_tractive_force(t: float, ph: float) -> float:
    """Tractive force: 150-260 kN normally, max 300."""
    return round(_clamp(210 + 50 * math.sin(t / 55 + ph) + random.gauss(0, 5), 0, 300), 1)


def _gen_fuel_liters(t: float, ph: float, tank_max: float = 1500) -> float:
    """Fuel drains slowly, wraps back to full for demo continuity."""
    drain_cycle = 7200  # 2-hour drain cycle
    drained = (t % drain_cycle) / drain_cycle * (tank_max - 200)
    return round(_clamp(tank_max - drained + random.gauss(0, 2), 0, tank_max), 1)


def _gen_brake_force(t: float, ph: float) -> float:
    """Brake force in kPa: mostly 0 (released), brief spikes when braking."""
    # Simulate periodic braking events
    cycle = 90  # braking event every ~90s
    phase_in_cycle = (t + ph * 30) % cycle
    if phase_in_cycle < 8:  # braking for 8s
        return round(_clamp(400 + 100 * (phase_in_cycle / 8) + random.gauss(0, 20), 0, 700), 1)
    return 0.0


# ---------------------------------------------------------------------------
# Per-type metric builders
# ---------------------------------------------------------------------------

def _build_metrics_kz8a(t: float, ph: float) -> list[dict]:
    speed = round(_clamp(80 + 25 * math.sin(t / 60 + ph) + random.gauss(0, 1.5), 0, 200), 2)
    motor_temp = round(_clamp(75 + 15 * math.sin(t / 45 + ph + 1.0) + random.gauss(0, 2), 0, 200), 1)
    pantograph_v = round(_clamp(25.0 + random.gauss(0, 0.3), 0, 35), 2)
    pressure_oil = round(_clamp(4.5 + 1.0 * math.sin(t / 60 + ph + 0.3) + random.gauss(0, 0.15), 0, 10), 2)

    return [
        {"key": "speed",              "name_ru": "Скорость",                      "unit": "км/ч", "current_value": speed},
        {"key": "temp_motor",         "name_ru": "Температура двигатель",         "unit": "°C",   "current_value": motor_temp},
        {"key": "temp_oil",           "name_ru": "Температура масла",             "unit": "°C",   "current_value": _gen_temp_oil(t, ph)},
        {"key": "temp_converters",    "name_ru": "Температура преобразователей",  "unit": "°C",   "current_value": _gen_temp_converters(t, ph)},
        {"key": "temp_air",           "name_ru": "Температура воздуха",           "unit": "°C",   "current_value": _gen_temp_air(t, ph)},
        {"key": "pantograph_voltage", "name_ru": "Напряжение контактной сети",    "unit": "кВ",   "current_value": pantograph_v},
        {"key": "pressure_oil",       "name_ru": "Масло в двигателе",             "unit": "бар",  "current_value": pressure_oil},
        {"key": "pressure_main_tank", "name_ru": "Главный резервуар",             "unit": "бар",  "current_value": _gen_pressure_main_tank(t, ph)},
        {"key": "pressure_brake",     "name_ru": "Тормоза",                       "unit": "бар",  "current_value": _gen_pressure_brake(t, ph)},
        {"key": "pressure_air",       "name_ru": "Воздух",                        "unit": "бар",  "current_value": _gen_pressure_air(t, ph)},
        {"key": "tractive_force",     "name_ru": "Тяговое усилие",                "unit": "кН",   "current_value": _gen_tractive_force(t, ph)},
        {"key": "fuel_liters",        "name_ru": "Топливо",                       "unit": "л",    "current_value": _gen_fuel_liters(t, ph)},
        {"key": "brake_force",        "name_ru": "Тормозное усилие",              "unit": "кПа",  "current_value": _gen_brake_force(t, ph)},
    ]


def _build_metrics_te33a(t: float, ph: float) -> list[dict]:
    speed = round(_clamp(60 + 20 * math.sin(t / 70 + ph) + random.gauss(0, 1.5), 0, 200), 2)
    motor_temp = round(_clamp(75 + 12 * math.sin(t / 55 + ph + 1.0) + random.gauss(0, 2), 0, 200), 1)
    oil_pressure = round(_clamp(5.0 + 1.2 * math.sin(t / 50 + ph) + random.gauss(0, 0.2), 0, 10), 2)

    return [
        {"key": "speed",               "name_ru": "Скорость",                      "unit": "км/ч", "current_value": speed},
        {"key": "temp_motor",          "name_ru": "Температура двигатель",         "unit": "°C",   "current_value": motor_temp},
        {"key": "temp_oil",            "name_ru": "Температура масла",             "unit": "°C",   "current_value": _gen_temp_oil(t, ph)},
        {"key": "temp_converters",     "name_ru": "Температура преобразователей",  "unit": "°C",   "current_value": _gen_temp_converters(t, ph)},
        {"key": "temp_air",            "name_ru": "Температура воздуха",           "unit": "°C",   "current_value": _gen_temp_air(t, ph)},
        {"key": "engine_oil_pressure", "name_ru": "Давление масла (двигатель)",    "unit": "бар",  "current_value": oil_pressure},
        {"key": "pressure_main_tank",  "name_ru": "Главный резервуар",             "unit": "бар",  "current_value": _gen_pressure_main_tank(t, ph)},
        {"key": "pressure_brake",      "name_ru": "Тормоза",                       "unit": "бар",  "current_value": _gen_pressure_brake(t, ph)},
        {"key": "pressure_air",        "name_ru": "Воздух",                        "unit": "бар",  "current_value": _gen_pressure_air(t, ph)},
        {"key": "tractive_force",      "name_ru": "Тяговое усилие",                "unit": "кН",   "current_value": _gen_tractive_force(t, ph)},
        {"key": "fuel_liters",         "name_ru": "Топливо",                       "unit": "л",    "current_value": _gen_fuel_liters(t, ph)},
        {"key": "brake_force",         "name_ru": "Тормозное усилие",              "unit": "кПа",  "current_value": _gen_brake_force(t, ph)},
    ]


# ---------------------------------------------------------------------------
# Telemetry generation
# ---------------------------------------------------------------------------

def generate_telemetry(loco: dict, t: float) -> dict:
    ph = loco["phase_offset"]
    route = ROUTES[loco["route_key"]]

    avg_speed_km_s = 90.0 / 3600.0
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
