from typing import Optional
from sqlmodel import SQLModel, Field
from .constants import UserRole


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    email: str = Field(unique=True, index=True)
    full_name: Optional[str] = None
    hashed_password: str
    role: UserRole = UserRole.DRIVER
    is_active: bool = True
    train_id: Optional[str] = None
