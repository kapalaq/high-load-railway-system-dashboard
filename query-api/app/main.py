from fastapi import FastAPI

from app.auth.router import auth_router
from app.database import db_lifespan

app = FastAPI(title="Railway Dashboard API", lifespan=db_lifespan)

app.include_router(auth_router)
