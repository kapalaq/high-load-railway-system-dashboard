from typing import Optional
from pydantic import BaseModel, EmailStr
from .constants import UserRole


class UserCreate(BaseModel):
    username: str
    email: EmailStr
    full_name: Optional[str] = None
    password: str
    role: UserRole = UserRole.DRIVER
    train_id: Optional[str] = None


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str]
    role: UserRole
    is_active: bool
    train_id: Optional[str]

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenUser(BaseModel):
    id: str
    role: UserRole
    train_id: Optional[str] = None
