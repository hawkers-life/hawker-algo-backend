"""
routers/ai_advisor.py — AI Strategy Advisor API endpoints.

POST /ai/analyse        → Analyse symbol and get full strategy suggestion
GET  /ai/status         → Check if AI is configured and working
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.user import User
from services.auth_service import get_current_user
from services.ai_advisor import get_ai_strategy_suggestion
from config import get_settings
from pydantic import BaseModel
from typing import Optional
from loguru import logger

router = APIRouter(prefix="/ai", tags=["AI Strategy Advisor"])
settings = get_settings()


class AIAnalyseRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    trading_style: str = "intraday"   # intraday | swing | options
    risk_tolerance: str = "moderate"  # conservative | moderate | aggressive
    capital: float = 100000.0


@router.post("/analyse")
def analyse_symbol(
    req: AIAnalyseRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Analyse a symbol with real market data and return an AI-generated
    trading strategy suggestion powered by Claude.

    Takes ~5-15 seconds (fetches live data + AI processing).
    """
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="AI Advisor not configured. Please add ANTHROPIC_API_KEY to your .env file. Get a free key at https://console.anthropic.com"
        )

    logger.info(f"🤖 AI analysis request: {req.symbol} | {req.trading_style} | user={current_user.email}")

    result = get_ai_strategy_suggestion(
        symbol         = req.symbol,
        exchange       = req.exchange,
        trading_style  = req.trading_style,
        risk_tolerance = req.risk_tolerance,
        capital        = req.capital,
    )

    if "error" in result and "_meta" not in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@router.get("/status")
def ai_status(current_user: User = Depends(get_current_user)):
    """Check if AI advisor is configured and ready."""
    has_key = bool(settings.ANTHROPIC_API_KEY)
    return {
        "configured": has_key,
        "model": "claude-sonnet-4-20250514",
        "message": "AI Advisor is ready." if has_key else
                   "ANTHROPIC_API_KEY not set. Add it to .env to enable AI features.",
        "setup_url": "https://console.anthropic.com" if not has_key else None,
    }
