import os
import time
import json
import random
import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
STREAM_NAME = os.environ.get("STREAM_NAME", "events")
INTERVAL = float(os.environ.get("INTERVAL", "2"))

SENSORS = ["sensor-A", "sensor-B", "sensor-C"]
EVENT_TYPES = ["temperature", "humidity", "pressure", "voltage"]

def generate_payload(seq: int) -> dict:
    return {
        "seq": seq,
        "sensor": random.choice(SENSORS),
        "type": random.choice(EVENT_TYPES),
        "value": round(random.uniform(0, 100), 3),
        "unit": random.choice(["°C", "%", "hPa", "V"]),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

def main():
    r = redis.from_url(REDIS_URL)
    print(f"[publisher] connected to {REDIS_URL}, streaming → {STREAM_NAME}")

    seq = 0
    while True:
        payload = generate_payload(seq)
        # XADD stores fields as key-value pairs; we use a single "data" field with JSON
        msg_id = r.xadd(STREAM_NAME, {"data": json.dumps(payload)})
        print(f"[publisher] sent id={msg_id.decode()} seq={seq} {payload}")
        seq += 1
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
