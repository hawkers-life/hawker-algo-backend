"""
routers/backtest.py — Backtesting API endpoints.

POST /backtest/run           → Run a backtest on historical data
GET  /backtest/strategies    → List available strategy keys
GET  /backtest/quote/{symbol} → Get live quote for a symbol
GET  /backtest/search        → Search for symbols
GET  /backtest/indices       → Get live index data
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_db
from models.user import User
from services.auth_service import get_current_user
from services.backtest_engine import run_backtest, STRATEGY_SIGNAL_MAP
from services.market_data import fetch_live_quote, get_index_data, search_symbols
from pydantic import BaseModel
from typing import Optional
from loguru import logger

router = APIRouter(prefix="/backtest", tags=["Backtesting"])


class BacktestRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    strategy_key: str = "ema_crossover"
    timeframe: str = "1d"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    days: int = 365
    initial_capital: float = 100000.0
    stop_loss_pct: float = 1.5
    target_pct: float = 3.0
    strategy_params: Optional[dict] = None


@router.post("/run")
def run_backtest_endpoint(
    req: BacktestRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Run a backtest on real NSE/BSE historical data.
    Takes 3-15 seconds depending on date range and timeframe.
    """
    # Validate inputs
    if req.days > 1825:   # 5 years max
        raise HTTPException(status_code=400, detail="Maximum date range is 5 years (1825 days)")
    if req.initial_capital < 10000:
        raise HTTPException(status_code=400, detail="Minimum capital is ₹10,000")
    if req.strategy_key not in STRATEGY_SIGNAL_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy '{req.strategy_key}'. Available: {list(STRATEGY_SIGNAL_MAP.keys())}"
        )

    logger.info(f"🔬 Backtest request: {req.symbol} | {req.strategy_key} | {req.days}d | user={current_user.email}")

    result = run_backtest(
        symbol          = req.symbol,
        exchange        = req.exchange,
        strategy_key    = req.strategy_key,
        timeframe       = req.timeframe,
        start_date      = req.start_date,
        end_date        = req.end_date,
        days            = req.days,
        initial_capital = req.initial_capital,
        stop_loss_pct   = req.stop_loss_pct,
        target_pct      = req.target_pct,
        strategy_params = req.strategy_params,
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@router.get("/strategies")
def list_backtest_strategies():
    """Return all available strategy keys and descriptions."""
    return {
        "strategies": [
            {"key": "ema_crossover",  "name": "EMA Crossover",            "description": "9/21 EMA crossover — trend following"},
            {"key": "rsi_reversal",   "name": "RSI Reversal",              "description": "Buy oversold (RSI<30), sell overbought (RSI>70)"},
            {"key": "vwap",           "name": "VWAP Breakout",             "description": "Trade price crossovers of VWAP"},
            {"key": "orb",            "name": "Opening Range Breakout",    "description": "Trade breakout of first 15-min range"},
            {"key": "macd",           "name": "MACD Crossover",            "description": "Buy/sell on MACD line crossing signal line"},
            {"key": "supertrend",     "name": "SuperTrend",                "description": "Trend-following with ATR-based trailing stop"},
        ]
    }


@router.get("/quote/{symbol}")
def get_quote(
    symbol: str,
    exchange: str = Query("NSE"),
    current_user: User = Depends(get_current_user),
):
    """Fetch live/delayed quote for any NSE/BSE symbol."""
    return fetch_live_quote(symbol, exchange)


@router.get("/indices")
def get_indices(current_user: User = Depends(get_current_user)):
    """Get live NIFTY, BankNifty, Sensex data."""
    return get_index_data()


@router.get("/search")
def symbol_search(
    q: str = Query(..., min_length=1),
    exchange: str = Query("NSE"),
    current_user: User = Depends(get_current_user),
):
    """Search for stock symbols."""
    results = search_symbols(q, exchange)
    return {"results": results, "query": q}
