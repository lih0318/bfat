"""Environment variables and application settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env: try backend/.env, project_root/.env, then cwd/.env (so any run directory works)
_backend_dir = Path(__file__).resolve().parent.parent.parent
_project_root = _backend_dir.parent
_cwd = Path.cwd()
_env_candidates = [
    _backend_dir / ".env",
    _project_root / ".env",
    _cwd / ".env",
]
_ENV_PATH = None
for p in _env_candidates:
    if p.exists():
        _ENV_PATH = p
        break
if _ENV_PATH is None:
    _ENV_PATH = _backend_dir / ".env"  # default path for pydantic; may not exist


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
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = False
