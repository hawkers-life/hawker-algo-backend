"""
routers/dashboard.py — Dashboard data endpoints.
GET /dashboard/summary    → Key metrics (P&L, positions, strategies)
GET /dashboard/equity-curve → Equity curve data points
GET /dashboard/recent-trades → Latest 10 trades
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from datetime import datetime, timezone, timedelta
from database import get_db
from models.user import User
from models.trade import Trade, TradeStatus, TradeMode
from models.strategy import Strategy, StrategyStatus
from models.subscription import RiskConfig
from services.auth_service import get_current_user

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary")
def get_dashboard_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns all data needed for the Dashboard page:
    - Today's P&L
    - Open positions count
    - Active strategies count
    - Capital deployed
    - Risk config status
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Today's P&L from filled/closed trades
    todays_pnl = db.query(func.coalesce(func.sum(Trade.pnl), 0.0)).filter(
        and_(
            Trade.user_id == current_user.id,
            Trade.placed_at >= today_start,
            Trade.status.in_([TradeStatus.FILLED, TradeStatus.SQUARED_OFF])
        )
    ).scalar()

    # Open positions
    open_positions = db.query(func.count(Trade.id)).filter(
        and_(Trade.user_id == current_user.id, Trade.status == TradeStatus.OPEN)
    ).scalar()

    # Active strategies
    active_strategies = db.query(func.count(Strategy.id)).filter(
        and_(
            Strategy.user_id == current_user.id,
            Strategy.status.in_([StrategyStatus.LIVE, StrategyStatus.PAPER_TRADING])
        )
    ).scalar()

    total_strategies = db.query(func.count(Strategy.id)).filter(
        Strategy.user_id == current_user.id
    ).scalar()

    # Capital deployed (sum of open position values)
    capital_deployed = db.query(func.coalesce(
        func.sum(Trade.entry_price * Trade.quantity), 0.0
    )).filter(
        and_(Trade.user_id == current_user.id, Trade.status == TradeStatus.OPEN)
    ).scalar()

    # Risk config
    risk_config = db.query(RiskConfig).filter(RiskConfig.user_id == current_user.id).first()

    # Today's trade count
    todays_trades = db.query(func.count(Trade.id)).filter(
        and_(Trade.user_id == current_user.id, Trade.placed_at >= today_start)
    ).scalar()

    return {
        "todays_pnl": round(float(todays_pnl), 2),
        "todays_pnl_pct": round((float(todays_pnl) / 400000) * 100, 2) if todays_pnl else 0,
        "open_positions": open_positions,
        "active_strategies": active_strategies,
        "total_strategies": total_strategies,
        "capital_deployed": round(float(capital_deployed), 2),
        "todays_trades": todays_trades,
        "trading_halted": risk_config.is_trading_halted if risk_config else False,
        "halt_reason": risk_config.halt_reason if risk_config else None,
        "max_daily_loss": risk_config.max_daily_loss if risk_config else 5000,
        "daily_loss_used": abs(float(todays_pnl)) if todays_pnl < 0 else 0,
    }


@router.get("/equity-curve")
def get_equity_curve(
    days: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Returns daily P&L for equity curve chart."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)

    daily_pnl = db.query(
        func.date(Trade.placed_at).label("date"),
        func.sum(Trade.pnl).label("daily_pnl")
    ).filter(
        and_(
            Trade.user_id == current_user.id,
            Trade.placed_at >= start_date,
            Trade.status.in_([TradeStatus.FILLED, TradeStatus.SQUARED_OFF])
        )
    ).group_by(func.date(Trade.placed_at)).order_by(func.date(Trade.placed_at)).all()

    # Build cumulative equity curve starting from 100
    equity = 100.0
    curve = []
    for row in daily_pnl:
        equity += (row.daily_pnl / 400000) * 100  # normalize to %
        curve.append({"date": str(row.date), "equity": round(equity, 2), "pnl": round(row.daily_pnl, 2)})

    # If no data, return flat line
    if not curve:
        return {"curve": [{"date": str(datetime.now(timezone.utc).date()), "equity": 100.0, "pnl": 0.0}]}

    return {"curve": curve}


@router.get("/recent-trades")
def get_recent_trades(
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Latest trades for dashboard trade table."""
    trades = db.query(Trade).filter(
        Trade.user_id == current_user.id
    ).order_by(Trade.placed_at.desc()).limit(min(limit, 50)).all()

    return {
        "trades": [
            {
                "id": str(t.id),
                "symbol": t.symbol,
                "action": t.action,
                "quantity": t.quantity,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": round(t.pnl, 2),
                "status": t.status,
                "mode": t.mode,
                "placed_at": t.placed_at.isoformat() if t.placed_at else None,
            }
            for t in trades
        ]
    }


@router.get("/strategy-performance")
def get_strategy_performance(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Performance summary per strategy for dashboard cards."""
    strategies = db.query(Strategy).filter(
        Strategy.user_id == current_user.id
    ).order_by(Strategy.created_at.desc()).all()

    return {
        "strategies": [
            {
                "id": str(s.id),
                "name": s.name,
                "strategy_type": s.strategy_type,
                "status": s.status,
                "total_trades": s.total_trades,
                "win_rate": round(s.win_rate, 1),
                "total_pnl": round(s.total_pnl, 2),
                "instrument": s.instrument,
            }
            for s in strategies
        ]
    }
