"""Environment variables and application settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment."""

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
