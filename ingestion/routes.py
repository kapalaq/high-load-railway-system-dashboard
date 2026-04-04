import logging

from fastapi import WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from models import TelemetryMessage

logger = logging.getLogger(__name__)


async def health(request: Request):
    try:
        await request.app.state.redis.ping()
        return {"status": "ok", "redis": "connected"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "redis": str(e)})


async def telemetry_ws(websocket: WebSocket):
    await websocket.accept()
    redis_client = websocket.app.state.redis
    stream_name = websocket.app.state.stream_name
    stream_maxlen = websocket.app.state.stream_maxlen
    client = websocket.client
    logger.info("Client connected: %s:%s", client.host, client.port)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                TelemetryMessage.model_validate_json(raw)
            except ValidationError as e:
                logger.warning("Validation error from %s:%s — %s", client.host, client.port, e)
                continue  # drop bad message, keep connection alive

            await redis_client.xadd(
                stream_name,
                {"payload": raw.encode()},
                maxlen=stream_maxlen,
                approximate=True,
            )
    except WebSocketDisconnect:
        logger.info("Client disconnected: %s:%s", client.host, client.port)
    except Exception as e:
        logger.error("Unexpected error from %s:%s — %s", client.host, client.port, e)
