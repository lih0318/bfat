"""
App configuration. Paths work for dev and Windows Standalone (packaged app).
"""
import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


def _config_dir() -> Path:
    """Config directory: CONFIG_DIR env, or APPDATA for packaged, or ./config relative to cwd."""
    env = os.environ.get("CONFIG_DIR")
    if env:
        return Path(env)
    # Windows Standalone: often run from app dir; APPDATA is reliable
    if os.name == "nt" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / "BinanceFuturesAutoTrader"
        base.mkdir(parents=True, exist_ok=True)
        return base
    # Dev: relative to backend (or cwd)
    return Path.cwd() / "config"


class Settings(BaseSettings):
    binance_api_key: str = ""
    binance_api_secret: str = ""
    fapi_base_url: str = "https://testnet.binancefuture.com"
    ws_base_url: Optional[str] = None  # derived from fapi_base_url if not set

    # App authentication (simple login)
    app_username: str = ""
    app_password: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def config_dir(self) -> Path:
        return _config_dir()

    @property
    def autopilot_config_path(self) -> Path:
        p = self.config_dir / "autopilot.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def journal_path(self) -> Path:
        p = self.config_dir / "journal.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
