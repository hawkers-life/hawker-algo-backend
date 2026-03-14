"""
config.py — All application settings loaded from environment variables.
Never hardcode secrets. All sensitive values come from .env file.
"""
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import List


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────
    APP_NAME: str = "Hawker Algo"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"          # development | production
    DEBUG: bool = False
    FRONTEND_URL: str = "http://localhost:5173"

    # ── Security ─────────────────────────────────────────
    SECRET_KEY: str                           # MUST be set in .env — 64+ char random string
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    BROKER_KEY_ENCRYPTION_KEY: str           # Fernet key for encrypting broker API keys

    # ── Database ─────────────────────────────────────────
    DATABASE_URL: str                         # postgresql://user:pass@host:5432/hawker_algo
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── CORS — Allowed Origins ────────────────────────────
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
    ]

    # ── Rate Limiting ─────────────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = 60          # General API
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 5     # Strict for login endpoint

    # ── Broker API Keys (encrypted at rest) ──────────────
    # Users store their own keys — these are platform-level defaults
    ZERODHA_API_KEY: str = ""
    ANGEL_API_KEY: str = ""
    DHAN_API_KEY: str = ""
    GROWW_API_KEY: str = ""

    # ── Notifications ─────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    TWILIO_ACCOUNT_SID: str = ""             # WhatsApp via Twilio
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_FROM: str = ""

    # ── Cashfree Payment ──────────────────────────────────
    CASHFREE_APP_ID: str = ""
    CASHFREE_SECRET_KEY: str = ""
    CASHFREE_ENVIRONMENT: str = "sandbox"   # sandbox | production

    # ── AI Strategy Advisor ───────────────────────────────
    ANTHROPIC_API_KEY: str = ""             # Get from console.anthropic.com

    # ── Market Data ───────────────────────────────────────
    # yfinance is free — no key needed for basic NSE data
    # For intraday data upgrade, add a paid provider key here
    MARKET_DATA_PROVIDER: str = "yfinance"  # yfinance | zerodha

    # ── Broker Credentials (Groww — Primary) ─────────────
    # Groww API — apply at: groww.in/stocks/developer-api
    GROWW_CLIENT_ID: str = ""
    GROWW_CLIENT_SECRET: str = ""
    GROWW_REDIRECT_URI: str = "http://localhost:5173/broker/callback"

    # ── Broker Credentials (Zerodha) ─────────────────────
    # Zerodha Kite Connect — apply at: kite.trade
    ZERODHA_API_KEY: str = ""
    ZERODHA_API_SECRET: str = ""

    # ── Broker Credentials (Angel One) ───────────────────
    # Angel SmartAPI — apply at: smartapi.angelbroking.com
    ANGEL_API_KEY: str = ""
    ANGEL_CLIENT_ID: str = ""
    ANGEL_MPIN: str = ""
    ANGEL_TOTP_SECRET: str = ""             # For TOTP-based login

    # ── Admin ─────────────────────────────────────────────
    ADMIN_EMAIL: str = ""
    FIRST_ADMIN_PASSWORD: str = ""          # Only used for initial setup

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Cached settings — loaded once at startup."""
    return Settings()
