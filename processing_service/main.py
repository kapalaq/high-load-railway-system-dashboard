import os
import json
import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
STREAM_NAME = os.environ.get("STREAM_NAME", "events")
BLOCK_MS = 2000  # block up to 2s waiting for new messages

def pretty(payload: dict) -> str:
    lines = [
        f"  seq    : {payload.get('seq')}",
        f"  sensor : {payload.get('sensor')}",
        f"  type   : {payload.get('type')}",
        f"  value  : {payload.get('value')} {payload.get('unit')}",
        f"  time   : {payload.get('timestamp')}",
    ]
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
                raw = fields.get(b"data", b"{}")
                payload = json.loads(raw)
                print(
                    f"\n[subscriber] message id={msg_id.decode()}\n{pretty(payload)}"
                )

if __name__ == "__main__":
    main()
