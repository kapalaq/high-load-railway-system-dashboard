import os

INGESTION_URL = os.getenv("INGESTION_URL", "ws://localhost:8001/ws/telemetry")
HZ = float(os.getenv("HZ", "0.01"))
RECONNECT_DELAY_S = float(os.getenv("RECONNECT_DELAY_S", "2"))

QUERY_API_WS_URL = os.getenv("QUERY_API_WS_URL", "ws://localhost:8000/api/websocket/ws")
QUERY_API_TOKEN  = os.getenv(
    "QUERY_API_TOKEN",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJleHAiOjE3NzU0MTQ1MjQsInN1YiI6IjEiLCJyb2xlIjoiZHJpdmVyIn0"
    ".jTTlaHdLWG0N0Dep8kn7Y54kbWuwBsB_mSA-7pmm2NQ",
)

ROUTES = {
    "AKA": {
        "route_name": "Astana - Karaganda - Almaty",
        "total_distance_km": 1211,
        "stops": [
            {"name": "Astana",    "distance_km": 0,    "status": "passed",   "latitude": 51.1811, "longitude": 71.4460},
            {"name": "Karaganda", "distance_km": 211,  "status": "upcoming", "latitude": 49.8047, "longitude": 73.0884},
            {"name": "Almaty",    "distance_km": 1211, "status": "upcoming", "latitude": 43.2220, "longitude": 76.8512},
        ],
    },
    "AKA_REV": {
        "route_name": "Almaty - Karaganda - Astana",
        "total_distance_km": 1211,
        "stops": [
            {"name": "Almaty",    "distance_km": 0,    "status": "passed",   "latitude": 43.2220, "longitude": 76.8512},
            {"name": "Karaganda", "distance_km": 1000, "status": "upcoming", "latitude": 49.8047, "longitude": 73.0884},
            {"name": "Astana",    "distance_km": 1211, "status": "upcoming", "latitude": 51.1811, "longitude": 71.4460},
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
