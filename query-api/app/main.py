from fastapi import FastAPI

from app.auth.router import auth_router
from app.database import db_lifespan
from app.websocket.router import ws_router

app = FastAPI(title="Railway Dashboard API", lifespan=db_lifespan)

app.include_router(auth_router)
app.include_router(ws_router)
