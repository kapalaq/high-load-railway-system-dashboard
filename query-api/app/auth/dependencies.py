from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from .config import auth_config
from .constants import UserRole
from .schemas import TokenUser

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenUser:
    try:
        payload = jwt.decode(
            token, auth_config.SECRET_KEY, algorithms=[auth_config.ALGORITHM]
        )
        user_id: str = payload.get("sub")
        role: str = payload.get("role")
        if user_id is None or role is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )
        return TokenUser(id=user_id, role=UserRole(role))
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )


def require_role(*roles: UserRole):
    def _check(user: TokenUser = Depends(get_current_user)) -> TokenUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _check


require_admin = require_role(UserRole.ADMIN)
require_dispatcher = require_role(UserRole.ADMIN, UserRole.DISPATCHER)
