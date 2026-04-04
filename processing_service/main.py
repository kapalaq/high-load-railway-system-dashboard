import os
import json
import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
STREAM_NAME = os.environ.get("STREAM_NAME", "telemetry:raw")
BLOCK_MS = 2000  # block up to 2s waiting for new messages

def pretty(payload: dict) -> str:
    route = payload.get("route_info", {})
    metrics = payload.get("telemetry_config", {}).get("metrics", [])
    lines = [
        f"  train_id  : {payload.get('train_id')}",
        f"  type      : {payload.get('locomotive_type')}",
        f"  time      : {payload.get('timestamp')}",
        f"  route     : {route.get('route_name')} "
        f"({route.get('current_position_km')} / {route.get('total_distance_km')} km)",
    ]
    for m in metrics:
        lines.append(f"  {m.get('key'):25s}: {m.get('current_value')} {m.get('unit')}")
    return "\n".join(lines)

def main():
    r = redis.from_url(REDIS_URL)
    print(f"[subscriber] connected to {REDIS_URL}, listening ← {STREAM_NAME}")

    # Start from the latest message arriving after we connect
    last_id = "$"

    while True:
        # XREAD blocks until new messages arrive or BLOCK_MS elapses
        results = r.xread({STREAM_NAME: last_id}, block=BLOCK_MS, count=10)
        if not results:
            continue

        for _stream, messages in results:
            for msg_id, fields in messages:
                last_id = msg_id
                raw = fields.get(b"payload", b"{}")
                payload = json.loads(raw)
                print(
                    f"\n[subscriber] message id={msg_id.decode()}\n{pretty(payload)}"
                )

if __name__ == "__main__":
    main()
