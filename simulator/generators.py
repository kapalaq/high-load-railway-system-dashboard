import math
import random
import time

from config import ROUTES


# ---------------------------------------------------------------------------
# Fault injection
# Each train gets independent fault state with up to 3 simultaneous faults.
# A "critical incident" (3 faults at once) fires periodically to push the
# health score below 40 ("Критично"). Each fault lasts 30-90s.
# ---------------------------------------------------------------------------

# (metric_key, lo, hi, severity_label)
# "critical" entries must land in the config's critical range.
_FAULT_TARGETS: dict[str, list[tuple]] = {
    "KZ8A": [
        ("temp_motor",         103.0, 118.0, "critical"),   # overheated:    >101°C,  penalty 25
        ("temp_oil",            96.0, 130.0, "critical"),   # overheated:    >96°C,   penalty 20
        ("pantograph_voltage",  14.0,  17.9, "critical"),   # critical_low:  <18kV,   penalty 22
        ("pressure_main_tank",   1.0,   4.4, "critical"),   # critical_low:  <4.4bar, penalty 30
        ("current_ampere",    2501.0, 2900.0, "critical"),  # critical:      >2500A,  penalty 25
        ("energy_usage",      1401.0, 1490.0, "critical"),  # critical:      >1400kWh,penalty 15
        ("temp_oil",            83.0,  95.0, "warning"),    # elevated
        ("current_ampere",    2050.0, 2490.0, "warning"),   # high
    ],
    "TE33A": [
        ("temp_motor",         103.0, 118.0, "critical"),
        ("temp_oil",            96.0, 130.0, "critical"),
        ("pressure_main_tank",   1.0,   4.4, "critical"),
        ("current_ampere",    1801.0, 1980.0, "critical"),  # critical: >1800A, penalty 25
        ("fuel_liters",          0.0,   99.0, "critical"),  # critical: <100L,  penalty 30
        ("temp_oil",            83.0,  95.0, "warning"),
        ("fuel_liters",        100.0,  290.0, "warning"),
    ],
}

# Preset "critical incident" combos: 3 faults that together push total penalty > 60.
# Each entry is a list of (key, lo, hi) tuples.
_CRITICAL_INCIDENTS: dict[str, list[list[tuple]]] = {
    "KZ8A": [
        [  # Overheating + pressure loss + electrical fault
            ("temp_motor",         105.0, 115.0),
            ("pressure_main_tank",   1.5,   4.0),
            ("current_ampere",    2550.0, 2800.0),
        ],
        [  # Oil overheat + pantograph + energy spike
            ("temp_oil",            97.0, 125.0),
            ("pantograph_voltage",  14.0,  17.5),
            ("energy_usage",      1410.0, 1490.0),
        ],
    ],
    "TE33A": [
        [
            ("temp_motor",         105.0, 115.0),
            ("pressure_main_tank",   1.5,   4.0),
            ("current_ampere",    1820.0, 1970.0),
        ],
        [
            ("temp_oil",            97.0, 125.0),
            ("fuel_liters",          5.0,  80.0),
            ("pressure_main_tank",   1.5,   3.5),
        ],
    ],
}

# Per-train state: list of active fault slots + next incident schedule
# Each slot: {"key": str | None, "value": float, "until": float}
_fault_state: dict[str, dict] = {}


def _get_fault_state(train_id: str) -> dict:
    if train_id not in _fault_state:
        _fault_state[train_id] = {
            "slots": [],                                          # active faults
            "next_incident_at": time.time() + random.uniform(5.0, 15.0),  # first incident fast
        }
    return _fault_state[train_id]


def _apply_faults(train_id: str, loco_type: str, metrics: list[dict]) -> list[dict]:
    """Fire a new incident if due, keep active faults, override metric values."""
    now = time.time()
    state = _get_fault_state(train_id)

    # Expire finished faults
    state["slots"] = [s for s in state["slots"] if now < s["until"]]

    # Fire a new incident if it's time and no faults are currently active
    if now >= state["next_incident_at"] and not state["slots"]:
        incidents = _CRITICAL_INCIDENTS.get(loco_type, [])
        if incidents:
            chosen = random.choice(incidents)
            duration = random.uniform(30.0, 60.0)
            for key, lo, hi in chosen:
                state["slots"].append({
                    "key":   key,
                    "value": round(random.uniform(lo, hi), 2),
                    "until": now + duration,
                })
        # Schedule next incident: 60-120s after this one ends
        state["next_incident_at"] = now + duration + random.uniform(60.0, 120.0)

    # Apply overrides
    overrides = {s["key"]: s["value"] for s in state["slots"]}
    for m in metrics:
        if m["key"] in overrides:
            base = overrides[m["key"]]
            m["current_value"] = round(base + random.gauss(0, base * 0.01), 2)

    return metrics


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


def _compute_stops(position_km: float, stops: list[dict]) -> list[dict]:
    result = []
    for stop in stops:
        d = stop["distance_km"]
        if position_km >= d + 5:
            status = "пройдено"
        elif abs(position_km - d) <= 5:
            status = "текущая"
        else:
            status = "впереди"
        result.append({
            "name": stop["name"],
            "distance_km": d,
            "status": status,
            "latitude": stop["latitude"],
            "longitude": stop["longitude"],
        })
    return result


def _interpolate_coords(position_km: float, stops: list[dict]) -> dict:
    """Linearly interpolate lat/lng for the current position between two stops."""
    for i in range(len(stops) - 1):
        s0, s1 = stops[i], stops[i + 1]
        if s0["distance_km"] <= position_km <= s1["distance_km"]:
            seg_len = s1["distance_km"] - s0["distance_km"]
            t = (position_km - s0["distance_km"]) / seg_len if seg_len > 0 else 0.0
            return {
                "latitude":  round(s0["latitude"]  + t * (s1["latitude"]  - s0["latitude"]),  6),
                "longitude": round(s0["longitude"] + t * (s1["longitude"] - s0["longitude"]), 6),
            }
    # Beyond last stop — return last stop coords
    last = stops[-1]
    return {"latitude": last["latitude"], "longitude": last["longitude"]}


def _gen_temp_oil(t: float, ph: float) -> float:
    """Oil temp: oscillates 55-91°C, regularly drifts into warning (>80°C)."""
    return round(_clamp(73 + 18 * math.sin(t / 50 + ph + 0.5) + random.gauss(0, 1.5), 40, 150), 1)


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
    """Tractive force: 155-275 kN, regularly enters high-traction warning (>260 kN)."""
    return round(_clamp(215 + 60 * math.sin(t / 55 + ph) + random.gauss(0, 5), 0, 300), 1)


def _gen_fuel_liters(t: float, ph: float, tank_max: float = 1500) -> float:
    """Fuel drains slowly, wraps back to full for demo continuity."""
    drain_cycle = 7200  # 2-hour drain cycle
    drained = (t % drain_cycle) / drain_cycle * (tank_max - 200)
    return round(_clamp(tank_max - drained + random.gauss(0, 2), 0, tank_max), 1)


def _gen_energy_usage(t: float, ph: float) -> float:
    """Cumulative energy consumption for electric loco, resets each 2h cycle.
    Reaches ~1400 kWh by end of cycle → last third sits in 'elevated' warning (1201-1400)."""
    drain_cycle = 7200
    consumed = (t % drain_cycle) / drain_cycle * 1400 + random.gauss(0, 5)
    return round(_clamp(consumed, 0, 1500), 1)


def _gen_current_kz8a(t: float, ph: float) -> float:
    """Traction current for electric loco: normally 800-2000 A, max 3000."""
    return round(_clamp(1400 + 400 * math.sin(t / 55 + ph + 0.2) + random.gauss(0, 30), 0, 3000), 0)


def _gen_current_te33a(t: float, ph: float) -> float:
    """Generator output current for diesel loco: normally 600-1500 A, max 2000."""
    return round(_clamp(1000 + 350 * math.sin(t / 60 + ph + 0.5) + random.gauss(0, 25), 0, 2000), 0)


def _gen_brake_force(t: float, ph: float) -> float:
    """Brake force in kPa: mostly 0 (released), brief spikes when braking."""
    cycle = 90  # braking event every ~90s
    phase_in_cycle = (t + ph * 30) % cycle
    if phase_in_cycle < 8:  # braking for 8s
        return round(_clamp(400 + 100 * (phase_in_cycle / 8) + random.gauss(0, 20), 0, 700), 1)
    return 0.0


def _build_metrics_kz8a(t: float, ph: float) -> list[dict]:
    speed = round(_clamp(80 + 25 * math.sin(t / 60 + ph) + random.gauss(0, 1.5), 0, 200), 2)
    motor_temp = round(_clamp(82 + 18 * math.sin(t / 45 + ph + 1.0) + random.gauss(0, 2), 0, 200), 1)
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
        {"key": "tractive_force",     "name_ru": "Тяговое усилие",                "unit": "кН",    "current_value": _gen_tractive_force(t, ph)},
        {"key": "energy_usage",       "name_ru": "Потребление энергии",           "unit": "кВт·ч", "current_value": _gen_energy_usage(t, ph)},
        {"key": "current_ampere",     "name_ru": "Ток",                           "unit": "А",     "current_value": _gen_current_kz8a(t, ph)},
        {"key": "brake_force",        "name_ru": "Тормозное усилие",              "unit": "кПа",   "current_value": _gen_brake_force(t, ph)},
    ]


def _build_metrics_te33a(t: float, ph: float) -> list[dict]:
    speed = round(_clamp(60 + 20 * math.sin(t / 70 + ph) + random.gauss(0, 1.5), 0, 200), 2)
    motor_temp = round(_clamp(82 + 18 * math.sin(t / 55 + ph + 1.0) + random.gauss(0, 2), 0, 200), 1)
    pressure_oil = round(_clamp(5.0 + 1.2 * math.sin(t / 50 + ph) + random.gauss(0, 0.2), 0, 10), 2)

    return [
        {"key": "speed",               "name_ru": "Скорость",                      "unit": "км/ч", "current_value": speed},
        {"key": "temp_motor",          "name_ru": "Температура двигатель",         "unit": "°C",   "current_value": motor_temp},
        {"key": "temp_oil",            "name_ru": "Температура масла",             "unit": "°C",   "current_value": _gen_temp_oil(t, ph)},
        {"key": "temp_converters",     "name_ru": "Температура преобразователей",  "unit": "°C",   "current_value": _gen_temp_converters(t, ph)},
        {"key": "temp_air",            "name_ru": "Температура воздуха",           "unit": "°C",   "current_value": _gen_temp_air(t, ph)},
        {"key": "pressure_oil",        "name_ru": "Масло в двигателе",             "unit": "бар",  "current_value": pressure_oil},
        {"key": "pressure_main_tank",  "name_ru": "Главный резервуар",             "unit": "бар",  "current_value": _gen_pressure_main_tank(t, ph)},
        {"key": "pressure_brake",      "name_ru": "Тормоза",                       "unit": "бар",  "current_value": _gen_pressure_brake(t, ph)},
        {"key": "pressure_air",        "name_ru": "Воздух",                        "unit": "бар",  "current_value": _gen_pressure_air(t, ph)},
        {"key": "tractive_force",      "name_ru": "Тяговое усилие",                "unit": "кН",   "current_value": _gen_tractive_force(t, ph)},
        {"key": "fuel_liters",         "name_ru": "Топливо",                       "unit": "л",    "current_value": _gen_fuel_liters(t, ph)},
        {"key": "current_ampere",      "name_ru": "Ток",                           "unit": "А",    "current_value": _gen_current_te33a(t, ph)},
        {"key": "brake_force",         "name_ru": "Тормозное усилие",              "unit": "кПа",  "current_value": _gen_brake_force(t, ph)},
    ]

def generate_telemetry(loco: dict, t: float) -> dict:
    ph = loco["phase_offset"]
    route = ROUTES[loco["route_key"]]

    avg_speed_km_s = 90.0 / 3600.0
    position_km = round((avg_speed_km_s * t + ph * 50) % route["total_distance_km"], 2)

    if loco["loco_type"] == "KZ8A":
        metrics = _build_metrics_kz8a(t, ph)
    else:
        metrics = _build_metrics_te33a(t, ph)

    metrics = _apply_faults(loco["train_id"], loco["loco_type"], metrics)

    stops = route["stops"]
    return {
        "train_id": loco["train_id"],
        "locomotive_type": loco["locomotive_type"],
        "timestamp": _iso_now(),
        "route_info": {
            "route_name": route["route_name"],
            "total_distance_km": route["total_distance_km"],
            "current_position_km": position_km,
            "current": _interpolate_coords(position_km, stops),
            "stops": _compute_stops(position_km, stops),
        },
        "telemetry_config": {
            "metrics": metrics,
        },
    }
