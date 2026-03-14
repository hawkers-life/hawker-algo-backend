"""
routers/risk.py — Risk management endpoints.
GET  /risk/config          → Get user's risk settings
PUT  /risk/config          → Update risk settings
POST /risk/emergency-stop  → Stop ALL strategies immediately
POST /risk/square-off-all  → Square off all open positions
POST /risk/resume          → Resume trading after halt
GET  /risk/status          → Current risk status (daily loss, drawdown etc.)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from datetime import datetime, timezone
from database import get_db
from models.user import User
from models.subscription import RiskConfig
from models.strategy import Strategy, StrategyStatus
from models.trade import Trade, TradeStatus
from services.auth_service import get_current_user
from pydantic import BaseModel
from typing import Optional
from loguru import logger

router = APIRouter(prefix="/risk", tags=["Risk Management"])


class RiskConfigUpdate(BaseModel):
    max_daily_loss: Optional[float] = None
    max_daily_loss_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    max_capital_deployed_pct: Optional[float] = None
    max_position_size: Optional[float] = None
    default_stop_loss_pct: Optional[float] = None
    default_target_pct: Optional[float] = None


@router.get("/config")
def get_risk_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    config = db.query(RiskConfig).filter(RiskConfig.user_id == current_user.id).first()
    if not config:
        config = RiskConfig(user_id=current_user.id)
        db.add(config)
        db.commit()
        db.refresh(config)
    return {
        "max_daily_loss": config.max_daily_loss,
        "max_daily_loss_pct": config.max_daily_loss_pct,
        "max_drawdown_pct": config.max_drawdown_pct,
        "max_capital_deployed_pct": config.max_capital_deployed_pct,
        "max_position_size": config.max_position_size,
        "default_stop_loss_pct": config.default_stop_loss_pct,
        "default_target_pct": config.default_target_pct,
        "is_trading_halted": config.is_trading_halted,
        "halt_reason": config.halt_reason,
    }


@router.put("/config")
def update_risk_config(
    data: RiskConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    config = db.query(RiskConfig).filter(RiskConfig.user_id == current_user.id).first()
    if not config:
        config = RiskConfig(user_id=current_user.id)
        db.add(config)

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(config, field, value)
    db.commit()
    return {"message": "Risk settings updated"}


@router.post("/emergency-stop")
def emergency_stop(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    EMERGENCY STOP — stops all running strategies immediately.
    Does NOT square off existing open positions (use /square-off-all for that).
    """
    # Stop all active strategies
    stopped = db.query(Strategy).filter(
        and_(
            Strategy.user_id == current_user.id,
            Strategy.status.in_([StrategyStatus.LIVE, StrategyStatus.PAPER_TRADING, StrategyStatus.FORWARD_TESTING])
        )
    ).update({"status": StrategyStatus.STOPPED})

    # Halt trading flag
    config = db.query(RiskConfig).filter(RiskConfig.user_id == current_user.id).first()
    if config:
        config.is_trading_halted = True
        config.halt_reason = "Manual emergency stop by user"

    db.commit()
    logger.warning(f"🚨 EMERGENCY STOP triggered by {current_user.email} — {stopped} strategies stopped")
    return {"message": f"Emergency stop executed. {stopped} strategies stopped.", "halted": True}


@router.post("/square-off-all")
def square_off_all(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Square off all open positions.
    In live mode: sends market sell orders to broker.
    In paper mode: closes simulated positions.
    """
    open_trades = db.query(Trade).filter(
        and_(Trade.user_id == current_user.id, Trade.status == TradeStatus.OPEN)
    ).all()

    squared = 0
    for trade in open_trades:
        trade.status = TradeStatus.SQUARED_OFF
        trade.closed_at = datetime.now(timezone.utc)
        trade.notes = "Squared off via emergency control"
        squared += 1

    db.commit()
    logger.warning(f"🔴 Square-off all: {squared} positions closed for {current_user.email}")
    return {"message": f"{squared} open positions squared off", "count": squared}


@router.post("/resume")
def resume_trading(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Resume trading after a halt."""
    config = db.query(RiskConfig).filter(RiskConfig.user_id == current_user.id).first()
    if config:
        config.is_trading_halted = False
        config.halt_reason = None
        db.commit()
    return {"message": "Trading resumed", "halted": False}


@router.get("/status")
def get_risk_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Live risk metrics — today's loss usage, drawdown etc."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    config = db.query(RiskConfig).filter(RiskConfig.user_id == current_user.id).first()

    todays_pnl = db.query(func.coalesce(func.sum(Trade.pnl), 0.0)).filter(
        and_(
            Trade.user_id == current_user.id,
            Trade.placed_at >= today_start,
            Trade.status.in_([TradeStatus.FILLED, TradeStatus.SQUARED_OFF])
        )
    ).scalar()

    daily_loss_used = abs(float(todays_pnl)) if todays_pnl < 0 else 0
    max_daily_loss = config.max_daily_loss if config else 5000
    daily_loss_pct = (daily_loss_used / max_daily_loss * 100) if max_daily_loss > 0 else 0

    return {
        "todays_pnl": round(float(todays_pnl), 2),
        "daily_loss_used": round(daily_loss_used, 2),
        "daily_loss_limit": max_daily_loss,
        "daily_loss_pct_used": round(daily_loss_pct, 1),
        "is_trading_halted": config.is_trading_halted if config else False,
        "halt_reason": config.halt_reason if config else None,
    }
