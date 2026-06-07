"""
Simple token-based authentication.
Login with APP_USERNAME / APP_PASSWORD -> receive a bearer token.
All other API endpoints require this token via middleware.
"""
import hashlib
import hmac
import secrets
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter()

# In-memory token store: token -> expiry_ts
_active_tokens: dict[str, float] = {}
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    ok: bool
    token: Optional[str] = None
    message: Optional[str] = None


def _generate_token() -> str:
    return secrets.token_hex(32)


def _verify_credentials(username: str, password: str) -> bool:
    expected_user = settings.app_username
    expected_pass = settings.app_password
    if not expected_user or not expected_pass:
        # No credentials configured -> reject all
        return False
    return hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_pass)


def validate_token(token: str) -> bool:
    """Check if token is valid and not expired."""
    if not token:
        return False
    expiry = _active_tokens.get(token)
    if expiry is None:
        return False
    if time.time() > expiry:
        _active_tokens.pop(token, None)
        return False
    return True


def cleanup_expired_tokens() -> None:
    now = time.time()
    expired = [t for t, exp in _active_tokens.items() if now > exp]
    for t in expired:
        _active_tokens.pop(t, None)


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    if not _verify_credentials(body.username, body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    # Cleanup old tokens periodically
    cleanup_expired_tokens()
    token = _generate_token()
    _active_tokens[token] = time.time() + TOKEN_TTL_SECONDS
    return LoginResponse(ok=True, token=token)


@router.post("/logout")
def logout(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        _active_tokens.pop(token, None)
    return {"ok": True}


@router.get("/check")
def check_auth(request: Request):
    """Check if the current token is still valid."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        if validate_token(token):
            return {"ok": True, "authenticated": True}
    return {"ok": True, "authenticated": False}
