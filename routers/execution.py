"""
routers/execution.py — Live trade execution endpoints.

POST /execution/order          → Place a new order (paper or live)
POST /execution/close/{id}     → Close/square off a specific trade
GET  /execution/positions      → Get open positions from broker
GET  /execution/funds          → Get available funds from broker
GET  /execution/broker-orders  → Get today's orders from broker
GET  /execution/broker-login-url → Get OAuth URL for broker login
POST /execution/broker-callback  → Handle broker OAuth callback
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_
from database import get_db
from models.user import User
from models.trade import Trade, TradeStatus
from models.strategy import BrokerAccount
from services.auth_service import get_current_user, require_sebi_accepted
from services.order_manager import execute_order, close_trade, RiskViolation
from services.broker_factory import get_broker_service, get_broker_login_url
from pydantic import BaseModel
from typing import Optional
from loguru import logger

router = APIRouter(prefix="/execution", tags=["Live Execution"])


class OrderRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    action: str                    # BUY | SELL
    quantity: int
    order_type: str = "MARKET"     # MARKET | LIMIT | SL | SL-M
    price: float = 0.0
    stop_loss_price: float = 0.0
    target_price: float = 0.0
    mode: str = "paper"            # paper | live | forward_test
    broker: str = "groww"          # groww | zerodha | angel_one
    strategy_id: Optional[str] = None


class CloseTradeRequest(BaseModel):
    exit_price: float


class BrokerCallbackRequest(BaseModel):
    broker: str
    auth_code: str
    state: Optional[str] = None


# ── Order placement ───────────────────────────────────────────────────────────

@router.post("/order")
def place_order(
    req: OrderRequest,
    current_user: User = Depends(require_sebi_accepted),
    db: Session = Depends(get_db),
):
    """
    Place a new order. Routes to paper simulation or live broker
    based on the `mode` field.
    """
    if req.quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be at least 1")
    if req.action not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="action must be BUY or SELL")

    try:
        result = execute_order(
            user_id         = str(current_user.id),
            strategy_id     = req.strategy_id,
            symbol          = req.symbol,
            exchange        = req.exchange,
            action          = req.action,
            quantity        = req.quantity,
            order_type      = req.order_type,
            price           = req.price,
            stop_loss_price = req.stop_loss_price,
            target_price    = req.target_price,
            mode            = req.mode,
            broker_name     = req.broker,
            db              = db,
        )
        return result

    except RiskViolation as rv:
        raise HTTPException(status_code=403, detail=str(rv))
    except Exception as e:
        logger.error(f"Order execution error: {e}")
        raise HTTPException(status_code=500, detail=f"Order failed: {str(e)}")


@router.post("/close/{trade_id}")
def close_trade_endpoint(
    trade_id: str,
    req: CloseTradeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Close a specific open trade at a given exit price."""
    # Ownership check
    trade = db.query(Trade).filter(
        and_(Trade.id == trade_id, Trade.user_id == current_user.id)
    ).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status not in (TradeStatus.OPEN, TradeStatus.FILLED):
        raise HTTPException(status_code=400, detail=f"Trade is already {trade.status}")

    result = close_trade(trade_id, req.exit_price, db)
    return result


# ── Broker data endpoints ─────────────────────────────────────────────────────

def _get_user_broker(user_id: str, broker_name: str, db: Session):
    """Helper: get connected broker account or raise 404."""
    account = db.query(BrokerAccount).filter(
        and_(
            BrokerAccount.user_id == user_id,
            BrokerAccount.broker  == broker_name,
            BrokerAccount.is_active == True,
            BrokerAccount.is_connected == True,
        )
    ).first()
    if not account:
        raise HTTPException(
            status_code=404,
            detail=f"No connected {broker_name} account. Please connect your broker first."
        )
    return account


@router.get("/positions")
def get_live_positions(
    broker: str = "groww",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch open positions directly from the broker."""
    account = _get_user_broker(str(current_user.id), broker, db)
    svc = get_broker_service(broker, account)
    try:
        return {"positions": svc.get_positions(), "broker": broker}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Broker API error: {str(e)}")


@router.get("/funds")
def get_funds(
    broker: str = "groww",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get available funds/margin from the broker."""
    account = _get_user_broker(str(current_user.id), broker, db)
    svc = get_broker_service(broker, account)
    try:
        return svc.get_funds()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Broker API error: {str(e)}")


@router.get("/broker-orders")
def get_broker_orders(
    broker: str = "groww",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get today's order book from the broker."""
    account = _get_user_broker(str(current_user.id), broker, db)
    svc = get_broker_service(broker, account)
    try:
        return {"orders": svc.get_orders(), "broker": broker}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Broker API error: {str(e)}")


# ── Broker OAuth flow ─────────────────────────────────────────────────────────

@router.get("/broker-login-url")
def get_broker_login_url_endpoint(
    broker: str,
    client_id: str,
    client_secret: str,
    current_user: User = Depends(get_current_user),
):
    """
    Step 1 of broker OAuth: returns the URL to redirect user to for login.
    Frontend opens this URL — user logs in on broker's site.
    """
    from config import get_settings
    settings = get_settings()
    try:
        url = get_broker_login_url(broker, client_id, client_secret, settings.GROWW_REDIRECT_URI)
        return {"login_url": url, "broker": broker}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/broker-callback")
def broker_oauth_callback(
    req: BrokerCallbackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Step 2 of broker OAuth: receive the auth_code and exchange it for an access token.
    Called after broker redirects back to our app.
    """
    from cryptography.fernet import Fernet
    from config import get_settings
    from services.broker_factory import get_broker_service
    from datetime import datetime, timezone

    settings = get_settings()

    # Get the pending (not yet connected) broker account
    account = db.query(BrokerAccount).filter(
        and_(
            BrokerAccount.user_id == current_user.id,
            BrokerAccount.broker  == req.broker,
            BrokerAccount.is_active == True,
        )
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Broker account not found. Please add broker credentials first.")

    svc = get_broker_service(req.broker, account)

    try:
        token_data = svc.exchange_code_for_token(req.auth_code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {str(e)}")

    # Encrypt and save access token
    f = Fernet(settings.BROKER_KEY_ENCRYPTION_KEY.encode())
    account.encrypted_access_token = f.encrypt(token_data["access_token"].encode()).decode()
    account.is_connected = True
    account.last_connected_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(f"✅ Broker OAuth complete: {req.broker} for {current_user.email}")
    return {"status": "connected", "broker": req.broker, "message": f"{req.broker} connected successfully!"}
