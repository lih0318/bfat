"""Auth routes: login, logout, me, refresh."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from api.deps import get_current_user
from app.config.settings import Settings


router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 1800


@router.post("/refresh")
def refresh(request: Request):
    cookie = request.cookies.get("refresh_token")
    if not cookie:
        raise HTTPException(status_code=401, detail="No refresh token")
    settings: Settings = request.app.state.settings
    payload = decode_token(cookie, settings.jwt_secret)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    username = payload.get("sub", "")
    if username != settings.app_username:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    access = create_access_token(username, settings.jwt_secret)
    return {"access_token": access, "token_type": "bearer", "expires_in": 1800}


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request):
    settings: Settings = request.app.state.settings
    uname = settings.app_username
    pwd_hash = getattr(settings, "_app_password_hash", None)
    if not uname or not settings.app_password:
        raise HTTPException(status_code=503, detail="Auth not configured")
    if not pwd_hash:
        pwd_hash = hash_password(settings.app_password)
        setattr(settings, "_app_password_hash", pwd_hash)
    if req.username != uname or not verify_password(req.password, pwd_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    secret = settings.jwt_secret
    access = create_access_token(req.username, secret)
    refresh = create_refresh_token(req.username, secret)
    resp = JSONResponse(content={
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": 1800,
    })
    resp.set_cookie(
        key="refresh_token",
        value=refresh,
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return resp


@router.post("/logout")
def logout(request: Request):
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(key="refresh_token", path="/")
    return resp


@router.get("/me")
def me(username: str = Depends(get_current_user)):
    return {"username": username}
