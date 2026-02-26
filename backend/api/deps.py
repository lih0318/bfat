"""Auth dependency for protected routes."""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.auth import decode_token


security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    token = None
    if credentials:
        token = credentials.credentials
    if not token:
        cookie_refresh = request.cookies.get("refresh_token")
        if cookie_refresh:
            raise HTTPException(
                status_code=401,
                detail="Access token expired. Use refresh token.",
            )
        raise HTTPException(status_code=401, detail="Not authenticated")
    settings = request.app.state.settings
    secret = settings.jwt_secret
    payload = decode_token(token, secret)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload.get("sub", "")
