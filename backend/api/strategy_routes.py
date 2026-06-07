"""Strategy preset routes."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.deps import get_current_user

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


class StrategyConfigUpdate(BaseModel):
    mode: Literal["TRENDING", "RANGING"]


@router.get("/config")
async def get_strategy_config(
    request: Request,
    _: str = Depends(get_current_user),
):
    svc = getattr(request.app.state, "engine_service", None)
    if svc is None or not hasattr(svc, "get_strategy_config"):
        raise HTTPException(status_code=503, detail="Engine service not configured")
    return svc.get_strategy_config()


@router.put("/config")
async def update_strategy_config(
    payload: StrategyConfigUpdate,
    request: Request,
    _: str = Depends(get_current_user),
):
    svc = getattr(request.app.state, "engine_service", None)
    if svc is None or not hasattr(svc, "set_strategy_mode"):
        raise HTTPException(status_code=503, detail="Engine service not configured")
    try:
        return svc.set_strategy_mode(payload.mode)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
