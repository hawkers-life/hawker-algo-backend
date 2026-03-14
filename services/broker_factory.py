"""
services/broker_factory.py — Broker factory and unified order manager.

Given a broker name + decrypted credentials, returns the right
broker service instance. All routers use this — they never import
broker classes directly.

Usage:
    service = get_broker_service("groww", credentials)
    result  = service.place_order(...)
"""
from typing import Optional
from loguru import logger
from cryptography.fernet import Fernet
from config import get_settings

settings = get_settings()


def _decrypt(value: Optional[str]) -> str:
    """Decrypt a broker API key stored in the database."""
    if not value:
        return ""
    try:
        f = Fernet(settings.BROKER_KEY_ENCRYPTION_KEY.encode())
        return f.decrypt(value.encode()).decode()
    except Exception:
        return value  # return as-is if not encrypted (dev mode)


def get_broker_service(broker_name: str, broker_account):
    """
    Returns an initialised broker service for the given broker.

    broker_account: BrokerAccount SQLAlchemy model instance
    """
    name = broker_name.lower()

    if name == "groww":
        from services.broker_groww import GrowwService
        service = GrowwService(
            client_id      = _decrypt(broker_account.encrypted_api_key) or settings.GROWW_CLIENT_ID,
            client_secret  = _decrypt(broker_account.encrypted_api_secret) or settings.GROWW_CLIENT_SECRET,
            redirect_uri   = settings.GROWW_REDIRECT_URI,
            access_token   = _decrypt(broker_account.encrypted_access_token) or None,
        )
        return service

    elif name == "zerodha":
        from services.broker_zerodha import ZerodhaService
        service = ZerodhaService(
            api_key      = _decrypt(broker_account.encrypted_api_key) or settings.ZERODHA_API_KEY,
            api_secret   = _decrypt(broker_account.encrypted_api_secret) or settings.ZERODHA_API_SECRET,
            access_token = _decrypt(broker_account.encrypted_access_token) or None,
        )
        return service

    elif name == "angel_one":
        from services.broker_angel import AngelOneService
        service = AngelOneService(
            api_key     = _decrypt(broker_account.encrypted_api_key) or settings.ANGEL_API_KEY,
            client_id   = broker_account.client_id or settings.ANGEL_CLIENT_ID,
            mpin        = _decrypt(broker_account.encrypted_api_secret) or settings.ANGEL_MPIN,
            totp_secret = _decrypt(broker_account.encrypted_access_token) or settings.ANGEL_TOTP_SECRET,
        )
        return service

    else:
        raise ValueError(f"Unsupported broker: {broker_name}. Supported: groww, zerodha, angel_one")


def get_broker_login_url(broker_name: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
    """Get the OAuth login URL for a broker (called before connection is saved)."""
    name = broker_name.lower()
    if name == "groww":
        from services.broker_groww import GrowwService
        svc = GrowwService(client_id, client_secret, redirect_uri)
        return svc.get_login_url()
    elif name == "zerodha":
        from services.broker_zerodha import ZerodhaService
        svc = ZerodhaService(client_id, client_secret)
        return svc.get_login_url()
    else:
        raise ValueError(f"OAuth login not supported for {broker_name}")
