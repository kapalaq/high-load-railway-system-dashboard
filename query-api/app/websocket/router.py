import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from app.auth.config import auth_config
from app.websocket.service import get_data_by_code

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
):
    await websocket.accept()

    try:
        user_id, role = _decode_token(token)
    except ValueError as e:
        await websocket.close(code=4001, reason=str(e))
        return

    logger.info("Authenticated WS connection: user_id=%s role=%s", user_id, role)

    try:
        while True:
            code = await websocket.receive_text()
            code = code.strip()
            logger.info("Received code '%s' from user %s", code, user_id)

            data = await get_data_by_code(code)
            await websocket.send_json({"type": "query_result", "code": code, "data": data})

    except WebSocketDisconnect:
        logger.info("User %s disconnected", user_id)
