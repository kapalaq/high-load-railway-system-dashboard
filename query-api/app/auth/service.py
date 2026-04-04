from fastapi import HTTPException, status
from sqlmodel import Session, select

from .models import User
from .schemas import UserCreate
from .utils import hash_password, verify_password


def register_user(data: UserCreate, session: Session) -> User:
    existing = session.exec(select(User).where(User.username == data.username)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )
    user = User(
        username=data.username,
        email=data.email,
        full_name=data.full_name,
        hashed_password=hash_password(data.password),
        role=data.role,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def authenticate_user(username: str, password: str, session: Session) -> User:
    user = session.exec(select(User).where(User.username == username)).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account inactive",
        )
    return user
