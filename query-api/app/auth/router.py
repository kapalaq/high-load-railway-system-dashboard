from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session

from app.database import get_session
from .schemas import Token, UserCreate, UserOut
from .service import authenticate_user, register_user
from .utils import create_access_token

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


@auth_router.post("/register", response_model=UserOut, status_code=201)
def register(data: UserCreate, session: Session = Depends(get_session)):
    user = register_user(data, session)
    return user


@auth_router.post("/token", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    user = authenticate_user(form.username, form.password, session)
    token = create_access_token(subject=user.id, role=user.role)
    return Token(access_token=token)
