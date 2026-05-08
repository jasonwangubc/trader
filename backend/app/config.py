from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://trader:trader@localhost:5432/trader"
    database_url_sync: str = "postgresql+psycopg2://trader:trader@localhost:5432/trader"

    # App
    app_env: str = "development"
    secret_key: str = "dev-only-change-me-before-prod"
    paper_mode_default: bool = True

    # Risk
    base_risk_pct: float = 0.0075
    max_risk_pct: float = 0.02

    # Questrade
    questrade_login_server: str = "https://login.questrade.com"
    questrade_refresh_token: str | None = None
    questrade_api_base: str | None = None

    # Notifications
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    # SEC EDGAR API user-agent (required by SEC; must include contact email)
    # Format: "AppName/version contact@example.com"
    edgar_user_agent: str = "trader-screener/1.0 contact@example.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()
