"""JWT authentication utilities. Requires PyJWT (pip install PyJWT). Do NOT install 'jwt' package."""

from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jwt import PyJWTError, decode as jwt_decode, encode as jwt_encode
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer

from app.config.settings import Settings

ACCESS_EXPIRE_MIN = 30
REFRESH_EXPIRE_DAYS = 7
ALGORITHM = "HS256"

security = HTTPBearer(auto_error=False)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)


def create_access_token(username: str, secret: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_EXPIRE_MIN)
    return jwt_encode(
        {"sub": username, "exp": expire, "type": "access"},
        secret,
        algorithm=ALGORITHM,
    )


def create_refresh_token(username: str, secret: str) -> str:
    expire = datetime.utcnow() + timedelta(days=REFRESH_EXPIRE_DAYS)
    return jwt_encode(
        {"sub": username, "exp": expire, "type": "refresh"},
        secret,
        algorithm=ALGORITHM,
    )


def decode_token(token: str, secret: str) -> Optional[dict]:
    try:
        return jwt_decode(token, secret, algorithms=[ALGORITHM])
    except PyJWTError:
        return None


def verify_password(plain: str, hashed: str) -> bool:
    # passlib로 생성한 $2b$... 해시와 동일하게 검증됨
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
