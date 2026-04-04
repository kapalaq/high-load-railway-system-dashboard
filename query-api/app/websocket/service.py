async def get_data_by_code(code: str) -> dict:
    # TODO: replace with real DB query
    return {
        "code": code,
        "name": "Almaty – Nur-Sultan Express",
        "status": "on_time",
        "departure": "08:30",
        "arrival": "14:45",
        "platform": 3,
        "cars": 12,
        "occupancy_percent": 74,
    }
