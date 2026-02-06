"""
Autopilot API: config, start/stop, status, activity log.
"""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.autopilot_config import AutopilotConfig
from app.services import autopilot_service

router = APIRouter()


class AutopilotConfigUpdate(BaseModel):
    max_usdt: float | None = None
    max_leverage: int | None = None
    daily_loss_limit_usdt: float | None = None
    symbol: str | None = None
    entry_tf: str | None = None
    trend_tf: str | None = None
    atr_period: int | None = None
    atr_sl_mult: float | None = None
    atr_tp_mult: float | None = None
    rsi_period: int | None = None
    rsi_long_min: float | None = None
    rsi_short_max: float | None = None
    volume_ratio_min: float | None = None
    reentry_cooldown_minutes: int | None = None
    allow_position_flip: bool | None = None
    flip_fee_bps: float | None = None
    flip_slippage_bps: float | None = None
    flip_min_edge_ratio: float | None = None
    trading_hours_utc: str | None = None


@router.get("/config")
def get_config() -> dict[str, Any]:
    cfg = autopilot_service.get_config()
    return cfg.model_dump()


@router.put("/config")
def put_config(update: AutopilotConfigUpdate):
    cfg = autopilot_service.get_config()
    d = cfg.model_dump()
    u = update.model_dump(exclude_none=True)
    d.update(u)
    new_cfg = AutopilotConfig.model_validate(d)
    autopilot_service.save_config(new_cfg)
    return {"ok": True, "config": new_cfg.model_dump()}


@router.post("/start")
def start_autopilot():
    result = autopilot_service.start_autopilot()
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "Failed"))
    return result


@router.post("/stop")
def stop_autopilot():
    return autopilot_service.stop_autopilot()


@router.get("/status")
def status():
    return autopilot_service.get_status()


@router.get("/activity")
def activity(limit: int = 100, mode: str = "all"):
    """mode: all | live. Live = real trades only."""
    if mode not in ("all", "live"):
        mode = "all"
    return autopilot_service.get_activity(limit=min(limit, 200), mode=mode)
