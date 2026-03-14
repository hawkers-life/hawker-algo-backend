"""
routers/strategies.py — Strategy management endpoints.
GET    /strategies           → List all user strategies
POST   /strategies           → Create new strategy
GET    /strategies/{id}      → Get single strategy
PUT    /strategies/{id}      → Update strategy
DELETE /strategies/{id}      → Delete strategy
POST   /strategies/{id}/start → Start strategy (paper/live)
POST   /strategies/{id}/stop  → Stop strategy
GET    /strategies/prebuilt   → Get system pre-built strategies
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional, List
from database import get_db
from models.user import User, SubscriptionPlan
from models.strategy import Strategy, StrategyStatus, StrategyType, BrokerName
from models.subscription import PLAN_LIMITS
from services.auth_service import get_current_user, require_sebi_accepted
from pydantic import BaseModel
from typing import Any, Dict
import uuid

router = APIRouter(prefix="/strategies", tags=["Strategies"])


# ── Pre-built strategies (system templates) ───────────────────────────────────
PREBUILT_STRATEGIES = [
    {
        "id": "prebuilt-ema-crossover",
        "name": "EMA Crossover",
        "description": "Buys when fast EMA crosses above slow EMA. Classic trend-following strategy for intraday.",
        "strategy_type": "intraday",
        "instrument": "NIFTY50",
        "timeframe": "15m",
        "stop_loss_pct": 1.5,
        "target_pct": 3.0,
        "indicators": {"ema_fast": 9, "ema_slow": 21},
        "backtest_win_rate": 64,
        "backtest_trades": 142,
        "is_prebuilt": True,
        "category": "Equity",
    },
    {
        "id": "prebuilt-vwap-intraday",
        "name": "VWAP Intraday",
        "description": "Trades based on price position relative to VWAP. Strong edge in intraday for NSE stocks.",
        "strategy_type": "intraday",
        "instrument": "NIFTY50",
        "timeframe": "5m",
        "stop_loss_pct": 1.0,
        "target_pct": 2.0,
        "indicators": {"vwap": True, "rsi_period": 14},
        "backtest_win_rate": 59,
        "backtest_trades": 203,
        "is_prebuilt": True,
        "category": "Equity",
    },
    {
        "id": "prebuilt-rsi-reversal",
        "name": "RSI Reversal",
        "description": "Buys oversold RSI < 30, sells overbought RSI > 70. Works well for swing trades.",
        "strategy_type": "swing",
        "instrument": "NIFTY50",
        "timeframe": "1d",
        "stop_loss_pct": 2.0,
        "target_pct": 5.0,
        "indicators": {"rsi_period": 14, "oversold": 30, "overbought": 70},
        "backtest_win_rate": 67,
        "backtest_trades": 89,
        "is_prebuilt": True,
        "category": "Equity",
    },
    {
        "id": "prebuilt-orb",
        "name": "Opening Range Breakout",
        "description": "Trades breakout of the first 15-minute candle range. High-probability intraday strategy.",
        "strategy_type": "intraday",
        "instrument": "BANKNIFTY",
        "timeframe": "15m",
        "stop_loss_pct": 1.0,
        "target_pct": 2.0,
        "indicators": {"orb_minutes": 15},
        "backtest_win_rate": 61,
        "backtest_trades": 176,
        "is_prebuilt": True,
        "category": "Equity",
    },
    {
        "id": "prebuilt-iron-condor",
        "name": "Iron Condor — Nifty",
        "description": "Sells OTM call and put spreads. Earns premium when Nifty stays range-bound.",
        "strategy_type": "options",
        "instrument": "NIFTY",
        "timeframe": "1d",
        "stop_loss_pct": 50.0,
        "target_pct": 30.0,
        "indicators": {"strategy": "iron_condor", "expiry": "weekly", "delta": 0.15},
        "backtest_win_rate": 71,
        "backtest_trades": 58,
        "is_prebuilt": True,
        "category": "F&O",
    },
    {
        "id": "prebuilt-straddle",
        "name": "BankNifty Straddle",
        "description": "Sells ATM straddle on BankNifty. Profits from time decay when IV is high.",
        "strategy_type": "options",
        "instrument": "BANKNIFTY",
        "timeframe": "1d",
        "stop_loss_pct": 40.0,
        "target_pct": 25.0,
        "indicators": {"strategy": "straddle", "expiry": "weekly"},
        "backtest_win_rate": 55,
        "backtest_trades": 44,
        "is_prebuilt": True,
        "category": "F&O",
    },
    {
        "id": "prebuilt-bull-call-spread",
        "name": "Bull Call Spread",
        "description": "Buy ATM call, sell OTM call. Limited risk bullish position on Nifty.",
        "strategy_type": "options",
        "instrument": "NIFTY",
        "timeframe": "1d",
        "stop_loss_pct": 100.0,
        "target_pct": 100.0,
        "indicators": {"strategy": "bull_call_spread", "expiry": "monthly"},
        "backtest_win_rate": 58,
        "backtest_trades": 72,
        "is_prebuilt": True,
        "category": "F&O",
    },
    {
        "id": "prebuilt-naked-breakout",
        "name": "Naked Buying — Breakout",
        "description": "Buys ATM call or put options on breakout from consolidation zone.",
        "strategy_type": "options",
        "instrument": "NIFTY",
        "timeframe": "15m",
        "stop_loss_pct": 30.0,
        "target_pct": 60.0,
        "indicators": {"strategy": "naked_buy", "breakout_atr": 1.5},
        "backtest_win_rate": 42,
        "backtest_trades": 120,
        "is_prebuilt": True,
        "category": "F&O",
    },
    {
        "id": "prebuilt-vwap-options",
        "name": "VWAP-Based Intraday Options",
        "description": "Buys Nifty options based on VWAP breakout/breakdown signals.",
        "strategy_type": "options",
        "instrument": "NIFTY",
        "timeframe": "5m",
        "stop_loss_pct": 25.0,
        "target_pct": 50.0,
        "indicators": {"vwap": True, "strategy": "options_on_vwap"},
        "backtest_win_rate": 53,
        "backtest_trades": 98,
        "is_prebuilt": True,
        "category": "F&O",
    },
    {
        "id": "prebuilt-strangle",
        "name": "Nifty Strangle",
        "description": "Sells OTM call and put options. Profits from low volatility and time decay.",
        "strategy_type": "options",
        "instrument": "NIFTY",
        "timeframe": "1d",
        "stop_loss_pct": 45.0,
        "target_pct": 28.0,
        "indicators": {"strategy": "strangle", "expiry": "weekly", "delta": 0.10},
        "backtest_win_rate": 60,
        "backtest_trades": 66,
        "is_prebuilt": True,
        "category": "F&O",
    },
]


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class StrategyCreate(BaseModel):
    name: str
    description: Optional[str] = None
    strategy_type: str
    instrument: str
    exchange: str = "NSE"
    timeframe: str = "15m"
    allocated_capital: float = 100000.0
    max_quantity: int = 1
    stop_loss_pct: float = 1.5
    target_pct: float = 3.0
    trailing_sl: bool = False
    trailing_sl_pct: Optional[float] = None
    entry_conditions: Optional[Dict[str, Any]] = None
    exit_conditions: Optional[Dict[str, Any]] = None
    indicators: Optional[Dict[str, Any]] = None


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    allocated_capital: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    target_pct: Optional[float] = None
    entry_conditions: Optional[Dict[str, Any]] = None
    exit_conditions: Optional[Dict[str, Any]] = None
    indicators: Optional[Dict[str, Any]] = None


class StartStrategyRequest(BaseModel):
    mode: str  # "paper" | "live" | "forward_test"
    broker: Optional[str] = None


# ── Helper: check plan limits ─────────────────────────────────────────────────

def check_strategy_limit(user: User, db: Session):
    plan = user.subscription_plan or "free"
    limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])["max_strategies"]
    current_count = db.query(Strategy).filter(Strategy.user_id == user.id).count()
    if current_count >= limit:
        raise HTTPException(
            status_code=403,
            detail=f"Your {plan.upper()} plan allows up to {limit} strategies. Please upgrade."
        )


def strategy_to_dict(s: Strategy) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "strategy_type": s.strategy_type,
        "instrument": s.instrument,
        "exchange": s.exchange,
        "timeframe": s.timeframe,
        "allocated_capital": s.allocated_capital,
        "max_quantity": s.max_quantity,
        "stop_loss_pct": s.stop_loss_pct,
        "target_pct": s.target_pct,
        "trailing_sl": s.trailing_sl,
        "status": s.status,
        "broker": s.broker,
        "total_trades": s.total_trades,
        "win_rate": s.win_rate,
        "total_pnl": s.total_pnl,
        "is_prebuilt": s.is_prebuilt,
        "entry_conditions": s.entry_conditions,
        "exit_conditions": s.exit_conditions,
        "indicators": s.indicators,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/prebuilt")
def get_prebuilt_strategies():
    """Get all system pre-built strategies (no auth required)."""
    return {"strategies": PREBUILT_STRATEGIES}


@router.get("")
def list_strategies(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    strategies = db.query(Strategy).filter(
        Strategy.user_id == current_user.id
    ).order_by(Strategy.created_at.desc()).all()
    return {"strategies": [strategy_to_dict(s) for s in strategies]}


@router.post("", status_code=201)
def create_strategy(
    data: StrategyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    check_strategy_limit(current_user, db)
    strategy = Strategy(
        user_id=current_user.id,
        name=data.name,
        description=data.description,
        strategy_type=data.strategy_type,
        instrument=data.instrument.upper(),
        exchange=data.exchange.upper(),
        timeframe=data.timeframe,
        allocated_capital=data.allocated_capital,
        max_quantity=data.max_quantity,
        stop_loss_pct=data.stop_loss_pct,
        target_pct=data.target_pct,
        trailing_sl=data.trailing_sl,
        trailing_sl_pct=data.trailing_sl_pct,
        entry_conditions=data.entry_conditions,
        exit_conditions=data.exit_conditions,
        indicators=data.indicators,
        status=StrategyStatus.DRAFT,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return strategy_to_dict(strategy)


@router.get("/{strategy_id}")
def get_strategy(
    strategy_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    s = db.query(Strategy).filter(
        Strategy.id == strategy_id, Strategy.user_id == current_user.id
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy_to_dict(s)


@router.put("/{strategy_id}")
def update_strategy(
    strategy_id: str,
    data: StrategyUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    s = db.query(Strategy).filter(
        Strategy.id == strategy_id, Strategy.user_id == current_user.id
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if s.status == StrategyStatus.LIVE:
        raise HTTPException(status_code=400, detail="Cannot edit a live strategy. Stop it first.")

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(s, field, value)
    db.commit()
    db.refresh(s)
    return strategy_to_dict(s)


@router.delete("/{strategy_id}", status_code=204)
def delete_strategy(
    strategy_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    s = db.query(Strategy).filter(
        Strategy.id == strategy_id, Strategy.user_id == current_user.id
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if s.status == StrategyStatus.LIVE:
        raise HTTPException(status_code=400, detail="Stop the strategy before deleting")
    db.delete(s)
    db.commit()


@router.post("/{strategy_id}/start")
def start_strategy(
    strategy_id: str,
    data: StartStrategyRequest,
    current_user: User = Depends(require_sebi_accepted),
    db: Session = Depends(get_db)
):
    """Start a strategy in paper/live/forward_test mode."""
    s = db.query(Strategy).filter(
        Strategy.id == strategy_id, Strategy.user_id == current_user.id
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")

    # Check live trading permission
    if data.mode == "live":
        plan = current_user.subscription_plan or "free"
        if not PLAN_LIMITS.get(plan, {}).get("live_trading", False):
            raise HTTPException(
                status_code=403,
                detail="Live trading not available on Free plan. Please upgrade."
            )
        if not data.broker:
            raise HTTPException(status_code=400, detail="Broker is required for live trading")
        s.broker = data.broker
        s.status = StrategyStatus.LIVE
    elif data.mode == "paper":
        s.status = StrategyStatus.PAPER_TRADING
    else:
        s.status = StrategyStatus.FORWARD_TESTING

    db.commit()
    return {"message": f"Strategy '{s.name}' started in {data.mode} mode", "status": s.status}


@router.post("/{strategy_id}/stop")
def stop_strategy(
    strategy_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    s = db.query(Strategy).filter(
        Strategy.id == strategy_id, Strategy.user_id == current_user.id
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    s.status = StrategyStatus.STOPPED
    db.commit()
    return {"message": f"Strategy '{s.name}' stopped", "status": s.status}
