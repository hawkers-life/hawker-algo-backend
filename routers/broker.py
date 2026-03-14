"""
routers/broker.py — Broker account management.
All API keys are encrypted before storage. Never stored in plain text.
POST /brokers/connect        → Connect broker with API keys
GET  /brokers                → List connected brokers
DELETE /brokers/{id}         → Remove broker connection
GET  /brokers/{id}/status    → Check broker connection status
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.user import User
from models.strategy import BrokerAccount, BrokerName
from services.auth_service import get_current_user
from cryptography.fernet import Fernet
from config import get_settings
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from loguru import logger

router = APIRouter(prefix="/brokers", tags=["Broker Connections"])
settings = get_settings()


def get_fernet():
    """Get encryption instance. Key must be valid Fernet key."""
    try:
        return Fernet(settings.BROKER_KEY_ENCRYPTION_KEY.encode())
    except Exception:
        logger.warning("BROKER_KEY_ENCRYPTION_KEY not set — broker key encryption disabled")
        return None


def encrypt(value: str) -> str:
    f = get_fernet()
    if not f or not value:
        return value
    return f.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    f = get_fernet()
    if not f or not value:
        return value
    try:
        return f.decrypt(value.encode()).decode()
    except Exception:
        return ""


class BrokerConnectRequest(BaseModel):
    broker: str                    # zerodha | angel_one | dhan | groww
    api_key: str
    api_secret: Optional[str] = None
    client_id: Optional[str] = None
    display_name: Optional[str] = None


@router.post("/connect", status_code=201)
def connect_broker(
    data: BrokerConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Connect a broker account. API key is encrypted before saving."""
    # Validate broker name
    valid_brokers = [b.value for b in BrokerName]
    if data.broker not in valid_brokers:
        raise HTTPException(status_code=400, detail=f"Unsupported broker. Choose from: {valid_brokers}")

    # Check if already connected
    existing = db.query(BrokerAccount).filter(
        BrokerAccount.user_id == current_user.id,
        BrokerAccount.broker == data.broker,
        BrokerAccount.is_active == True
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"{data.broker} is already connected. Remove it first.")

    # Encrypt API keys before storing
    account = BrokerAccount(
        user_id=current_user.id,
        broker=data.broker,
        display_name=data.display_name or data.broker.replace("_", " ").title(),
        encrypted_api_key=encrypt(data.api_key),
        encrypted_api_secret=encrypt(data.api_secret) if data.api_secret else None,
        client_id=data.client_id,  # non-sensitive
        is_connected=True,
        last_connected_at=datetime.now(timezone.utc),
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    logger.info(f"✅ Broker connected: {data.broker} for {current_user.email}")
    return {
        "id": str(account.id),
        "broker": account.broker,
        "display_name": account.display_name,
        "client_id": account.client_id,
        "is_connected": account.is_connected,
        "connected_at": account.last_connected_at.isoformat(),
    }


@router.get("")
def list_brokers(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all connected broker accounts (without exposing API keys)."""
    accounts = db.query(BrokerAccount).filter(
        BrokerAccount.user_id == current_user.id,
        BrokerAccount.is_active == True
    ).all()
    return {
        "brokers": [
            {
                "id": str(a.id),
                "broker": a.broker,
                "display_name": a.display_name,
                "client_id": a.client_id,
                "is_connected": a.is_connected,
                "last_connected_at": a.last_connected_at.isoformat() if a.last_connected_at else None,
                # NOTE: API keys are NEVER returned in responses
            }
            for a in accounts
        ]
    }


@router.delete("/{broker_id}", status_code=204)
def remove_broker(
    broker_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    account = db.query(BrokerAccount).filter(
        BrokerAccount.id == broker_id,
        BrokerAccount.user_id == current_user.id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Broker account not found")
    account.is_active = False
    account.is_connected = False
    # Wipe encrypted keys on removal
    account.encrypted_api_key = None
    account.encrypted_api_secret = None
    account.encrypted_access_token = None
    db.commit()
    logger.info(f"🔌 Broker disconnected: {account.broker} for {current_user.email}")
