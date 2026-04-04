import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from app.auth.config import auth_config
from app.websocket.manager import ConnectionManager

logger = logging.getLogger("uvicorn")

ws_router = APIRouter(prefix="/api/websocket", tags=["websocket"])


def _decode_token(token: str):
    try:
        payload = jwt.decode(token, auth_config.SECRET_KEY, algorithms=[auth_config.ALGORITHM])
        user_id = payload.get("sub")
        role = payload.get("role")
        if user_id is None or role is None:
            raise ValueError("Missing claims")
        return user_id, role
    except JWTError as e:
        raise ValueError("Invalid token") from e


@ws_router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token"),
    train_id: str = Query(..., description="Train ID to subscribe to"),
):
    await websocket.accept()

    try:
        user_id, role = _decode_token(token)
    except ValueError as e:
        await websocket.close(code=4001, reason=str(e))
        return

    manager: ConnectionManager = websocket.app.state.manager
    await manager.subscribe(train_id, websocket)
    logger.info("WS connected: user_id=%s train_id=%s", user_id, train_id)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.unsubscribe(train_id, websocket)
        logger.info("WS disconnected: user_id=%s train_id=%s", user_id, train_id)
