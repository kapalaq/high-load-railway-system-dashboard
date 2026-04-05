"""
Railway Dashboard WebSocket client.

Usage:
    python client.py --train-id KZ8A-L001 [--url ws://localhost:8000/api/websocket/ws]

Subscribes to live updates for the given train and prints server responses.
Press Ctrl+C to exit.
"""

import argparse
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("Missing dependency: run  pip install websockets")
    sys.exit(1)


DEFAULT_URL = "ws://127.0.0.1:8000/api/websocket/ws"


def _fmt(data: dict) -> str:
    """Pretty-print a response dict."""
    msg_type = data.get("type", "unknown")

    if msg_type == "heartbeat":
        return "♥  heartbeat"

    if msg_type == "query_result":
        code = data.get("code", "?")
        inner = data.get("data", {})
        lines = [f"┌─ Query result for code: {code}"]
        for key, val in inner.items():
            lines.append(f"│  {key:<20} {val}")
        lines.append("└" + "─" * 40)
        return "\n".join(lines)

    # Fallback: dump JSON
    return json.dumps(data, indent=2, ensure_ascii=False)


async def _listen(ws: "websockets.ClientConnection", stop: asyncio.Event) -> None:
    """Background task: receive and print all incoming messages."""
    try:
        async for raw in ws:
            if stop.is_set():
                break
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"type": "raw", "body": raw}

            # Skip heartbeats — uncomment the line below to see them
            if data.get("type") == "heartbeat":
                continue

            print(f"\n{_fmt(data)}")
            print("Code> ", end="", flush=True)

    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"\n[connection closed with error: {e}]")


async def run(url: str, train_id: str) -> None:
    full_url = f"{url}?train_id={train_id}"
    print(f"Connecting to {url} for train {train_id} …")

    try:
        async with websockets.connect(full_url) as ws:
            print("Connected. Listening for updates. (Ctrl+C to exit)\n")

            stop = asyncio.Event()
            listener = asyncio.create_task(_listen(ws, stop))

            try:
                await listener

            except (EOFError, KeyboardInterrupt):
                print("\nInterrupted.")

            finally:
                stop.set()
                listener.cancel()
                try:
                    await listener
                except asyncio.CancelledError:
                    pass

    except websockets.exceptions.InvalidStatus as e:
        # Server rejected with an HTTP error code (e.g. 403, 422)
        print(f"Connection rejected: {e}")
        sys.exit(1)
    except websockets.exceptions.InvalidMessage as e:
        # Server closed the connection before completing the HTTP upgrade.
        # Most common cause: the server is not running or failed during startup
        # (e.g. database unreachable → lifespan error).
        print(f"Server did not complete the WebSocket handshake: {e}")
        print("Make sure the server is running: docker-compose up  OR  uvicorn app.main:app --reload")
        sys.exit(1)
    except OSError as e:
        print(f"Could not connect: {e}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Railway Dashboard WS client")
    parser.add_argument("--train-id", required=True, help="Train ID to subscribe to")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"WebSocket URL (default: {DEFAULT_URL})")
    args = parser.parse_args()

    asyncio.run(run(args.url, args.train_id))


if __name__ == "__main__":
    main()