"""Environment variables and application settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env: try backend/.env, then project_root/.env (parent of backend)
_backend_dir = Path(__file__).resolve().parent.parent.parent
_project_root = _backend_dir.parent
_ENV_PATH = _backend_dir / ".env" if (_backend_dir / ".env").exists() else _project_root / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=_ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./bfat.db"
    log_level: str = "INFO"
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True
    bfat_symbol: str = "BTCUSDT"
    app_username: str = ""
    app_password: str = ""
    jwt_secret: str = "change-me-in-production"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
