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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
