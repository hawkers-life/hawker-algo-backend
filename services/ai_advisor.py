"""
services/ai_advisor.py — AI Strategy Advisor powered by Anthropic Claude.

Analyses real market data (fetched live from yfinance) and suggests
the best strategy, entry/exit points, and risk parameters.

Workflow:
  1. Fetch live OHLCV + indicators for the requested symbol
  2. Build a rich market context prompt
  3. Send to Claude claude-sonnet-4-6 via Anthropic API
  4. Parse structured JSON response
  5. Return suggestion to frontend
"""
import anthropic
import json
import pandas as pd
import numpy as np
from datetime import datetime
from loguru import logger
import pandas_ta as ta
from services.market_data import fetch_historical, fetch_live_quote, get_index_data
from config import get_settings

settings = get_settings()


def _build_indicator_summary(df: pd.DataFrame) -> dict:
    """Calculate key technical indicators and return as dict for AI context."""
    if df.empty or len(df) < 30:
        return {}

    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]

    try:
        ema9  = ta.ema(close, length=9)
        ema21 = ta.ema(close, length=21)
        ema50 = ta.ema(close, length=50)
        rsi14 = ta.rsi(close, length=14)
        macd  = ta.macd(close)
        bb    = ta.bbands(close, length=20)
        atr   = ta.atr(high, low, close, length=14)
        vwap  = ta.vwap(high, low, close, vol)

        last = -1  # last row index

        # Price vs moving averages
        ltp = float(close.iloc[last])
        e9  = float(ema9.iloc[last])  if ema9 is not None and not pd.isna(ema9.iloc[last])  else None
        e21 = float(ema21.iloc[last]) if ema21 is not None and not pd.isna(ema21.iloc[last]) else None
        e50 = float(ema50.iloc[last]) if ema50 is not None and not pd.isna(ema50.iloc[last]) else None

        # RSI
        rsi_val = float(rsi14.iloc[last]) if rsi14 is not None and not pd.isna(rsi14.iloc[last]) else 50

        # MACD
        macd_val = float(macd["MACD_12_26_9"].iloc[last])   if macd is not None else 0
        macd_sig = float(macd["MACDs_12_26_9"].iloc[last])  if macd is not None else 0
        macd_hist= float(macd["MACDh_12_26_9"].iloc[last])  if macd is not None else 0

        # Bollinger Bands
        bb_upper = float(bb["BBU_20_2.0"].iloc[last]) if bb is not None else ltp * 1.02
        bb_lower = float(bb["BBL_20_2.0"].iloc[last]) if bb is not None else ltp * 0.98
        bb_mid   = float(bb["BBM_20_2.0"].iloc[last]) if bb is not None else ltp

        # ATR (volatility)
        atr_val  = float(atr.iloc[last]) if atr is not None and not pd.isna(atr.iloc[last]) else 0
        atr_pct  = (atr_val / ltp * 100) if ltp > 0 else 0

        # VWAP
        vwap_val = float(vwap.iloc[last]) if vwap is not None and not pd.isna(vwap.iloc[last]) else ltp

        # Volume analysis
        avg_vol_20 = float(vol.tail(20).mean())
        today_vol  = float(vol.iloc[last])
        vol_ratio  = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1

        # Trend detection
        trend = "neutral"
        if e9 and e21 and e50:
            if ltp > e9 > e21 > e50:
                trend = "strong_uptrend"
            elif ltp > e21 > e50:
                trend = "uptrend"
            elif ltp < e9 < e21 < e50:
                trend = "strong_downtrend"
            elif ltp < e21 < e50:
                trend = "downtrend"

        # Recent price change
        change_5d  = ((ltp - float(close.iloc[-6])) / float(close.iloc[-6]) * 100) if len(close) >= 6 else 0
        change_20d = ((ltp - float(close.iloc[-21])) / float(close.iloc[-21]) * 100) if len(close) >= 21 else 0

        return {
            "ltp": round(ltp, 2),
            "ema9": round(e9, 2) if e9 else None,
            "ema21": round(e21, 2) if e21 else None,
            "ema50": round(e50, 2) if e50 else None,
            "rsi": round(rsi_val, 1),
            "macd": round(macd_val, 3),
            "macd_signal": round(macd_sig, 3),
            "macd_histogram": round(macd_hist, 3),
            "bb_upper": round(bb_upper, 2),
            "bb_lower": round(bb_lower, 2),
            "bb_mid": round(bb_mid, 2),
            "atr": round(atr_val, 2),
            "atr_pct": round(atr_pct, 2),
            "vwap": round(vwap_val, 2),
            "volume_ratio": round(vol_ratio, 2),
            "trend": trend,
            "price_vs_vwap": "above" if ltp > vwap_val else "below",
            "price_vs_bb_mid": "above" if ltp > bb_mid else "below",
            "change_5d_pct": round(change_5d, 2),
            "change_20d_pct": round(change_20d, 2),
        }
    except Exception as e:
        logger.error(f"Indicator calculation error: {e}")
        return {"ltp": float(close.iloc[-1]) if not close.empty else 0}


def _determine_market_regime(indicators: dict, index_data: dict) -> str:
    """Classify current market regime based on indicators."""
    nifty = index_data.get("NIFTY 50", {})
    nifty_chg = nifty.get("change_pct", 0)

    rsi = indicators.get("rsi", 50)
    trend = indicators.get("trend", "neutral")
    vol_ratio = indicators.get("volume_ratio", 1)
    atr_pct = indicators.get("atr_pct", 1)

    if "downtrend" in trend and nifty_chg < -0.5:
        return "bearish_trending"
    elif "uptrend" in trend and nifty_chg > 0.5:
        return "bullish_trending"
    elif atr_pct < 0.5 and abs(nifty_chg) < 0.3:
        return "low_volatility_range_bound"
    elif atr_pct > 2.0 or abs(nifty_chg) > 1.5:
        return "high_volatility"
    else:
        return "neutral_consolidating"


def get_ai_strategy_suggestion(
    symbol: str,
    exchange: str = "NSE",
    trading_style: str = "intraday",  # intraday | swing | options
    risk_tolerance: str = "moderate",  # conservative | moderate | aggressive
    capital: float = 100_000.0,
) -> dict:
    """
    Main function: analyses symbol with real market data and
    returns AI-generated strategy suggestion via Claude.
    """
    if not settings.ANTHROPIC_API_KEY:
        return {
            "error": "ANTHROPIC_API_KEY not configured. Please add it to your .env file.",
            "setup_guide": "Get your free API key at: https://console.anthropic.com"
        }

    # ── 1. Gather market data ─────────────────────────────────────────────────
    logger.info(f"🤖 AI Advisor analysing {symbol} for {trading_style} trading...")

    # Fetch different timeframes for multi-timeframe analysis
    df_daily  = fetch_historical(symbol, exchange, "1d", days=90)
    df_weekly = fetch_historical(symbol, exchange, "1wk", days=365)

    if df_daily.empty:
        return {"error": f"Could not fetch market data for {symbol}. Please check the symbol name."}

    # Calculate indicators
    daily_indicators  = _build_indicator_summary(df_daily)
    weekly_indicators = _build_indicator_summary(df_weekly)
    index_data        = get_index_data()
    live_quote        = fetch_live_quote(symbol, exchange)
    market_regime     = _determine_market_regime(daily_indicators, index_data)

    ltp = daily_indicators.get("ltp", live_quote.get("ltp", 0))

    # ── 2. Build prompt ───────────────────────────────────────────────────────
    prompt = f"""You are a senior quantitative analyst and algorithmic trading expert specializing in Indian stock markets (NSE/BSE).

Analyse the following real-time market data for {symbol.upper()} ({exchange}) and provide a detailed, actionable strategy suggestion.

═══ LIVE MARKET DATA ═══
Symbol: {symbol.upper()} | Exchange: {exchange}
Current Price (LTP): ₹{ltp}
Trading Style Requested: {trading_style.upper()}
Risk Tolerance: {risk_tolerance.upper()}
Available Capital: ₹{capital:,.0f}

═══ DAILY TECHNICAL INDICATORS ═══
{json.dumps(daily_indicators, indent=2)}

═══ WEEKLY TREND CONTEXT ═══
Weekly RSI: {weekly_indicators.get('rsi', 'N/A')}
Weekly Trend: {weekly_indicators.get('trend', 'N/A')}
Weekly EMA50: {weekly_indicators.get('ema50', 'N/A')}
20-day price change: {weekly_indicators.get('change_20d_pct', 'N/A')}%

═══ MARKET CONTEXT ═══
NIFTY 50: ₹{index_data.get('NIFTY 50', {}).get('ltp', 'N/A')} ({index_data.get('NIFTY 50', {}).get('change_pct', 0):+.2f}%)
BANKNIFTY: ₹{index_data.get('BANKNIFTY', {}).get('ltp', 'N/A')} ({index_data.get('BANKNIFTY', {}).get('change_pct', 0):+.2f}%)
Market Regime: {market_regime.upper().replace('_', ' ')}

═══ YOUR TASK ═══
Provide a complete trading strategy recommendation. You MUST respond in valid JSON only, no other text.

The JSON must follow this exact structure:
{{
  "symbol": "{symbol.upper()}",
  "market_regime": "description of current market condition",
  "overall_bias": "BULLISH or BEARISH or NEUTRAL",
  "confidence": "HIGH or MEDIUM or LOW",
  "recommended_strategy": {{
    "name": "Strategy name",
    "type": "intraday or swing or options",
    "description": "Clear 2-3 sentence explanation of the strategy and WHY it suits current market conditions",
    "entry_trigger": "Exact condition that should trigger entry (be specific with price levels or indicator values)",
    "entry_price_range": {{"low": 0, "high": 0}},
    "stop_loss": {{"price": 0, "pct": 0, "reason": "why this stop loss level"}},
    "target_1": {{"price": 0, "pct": 0}},
    "target_2": {{"price": 0, "pct": 0}},
    "target_3": {{"price": 0, "pct": 0}},
    "position_size_pct": 0,
    "suggested_quantity": 0,
    "timeframe": "best timeframe for this setup",
    "hold_duration": "estimated hold time e.g. same day, 3-5 days, 2-3 weeks",
    "risk_reward_ratio": 0.0
  }},
  "key_levels": {{
    "strong_support": [0, 0],
    "strong_resistance": [0, 0],
    "vwap": {daily_indicators.get('vwap', ltp)},
    "day_pivot": 0
  }},
  "indicators_summary": {{
    "rsi_signal": "Oversold/Overbought/Neutral with brief explanation",
    "macd_signal": "Bullish/Bearish crossover or divergence explanation",
    "ema_signal": "Trend direction based on EMA alignment",
    "volume_signal": "Volume analysis — confirm or deny the move",
    "bollinger_signal": "Price position within bands"
  }},
  "risk_warnings": ["specific warning 1", "specific warning 2"],
  "best_prebuilt_strategy": "ema_crossover or rsi_reversal or vwap or orb or macd or supertrend",
  "backtest_suggestion": {{
    "strategy_key": "one of the above keys",
    "recommended_params": {{}},
    "suggested_days": 180
  }},
  "alternative_strategy": {{
    "name": "Alternative if primary doesn't trigger",
    "brief": "One sentence"
  }},
  "sebi_disclaimer": "For educational purposes only. Not investment advice."
}}

Use the actual price data provided. All prices must be realistic relative to the current LTP of ₹{ltp}. 
Calculate position size as: floor(capital × position_size_pct / entry_price).
Calculate risk-reward as: (target_1_price - entry_price) / (entry_price - stop_loss_price).
"""

    # ── 3. Call Anthropic API ─────────────────────────────────────────────────
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        raw_response = message.content[0].text.strip()

        # Clean JSON (remove markdown fences if present)
        if raw_response.startswith("```"):
            raw_response = raw_response.split("```")[1]
            if raw_response.startswith("json"):
                raw_response = raw_response[4:]
        raw_response = raw_response.strip()

        suggestion = json.loads(raw_response)

        # Attach metadata
        suggestion["_meta"] = {
            "generated_at": datetime.now().isoformat(),
            "symbol": symbol,
            "exchange": exchange,
            "trading_style": trading_style,
            "data_bars_used": len(df_daily),
            "model": "claude-sonnet-4-20250514",
        }

        logger.info(f"✅ AI suggestion generated for {symbol}: {suggestion.get('overall_bias')} / {suggestion.get('confidence')} confidence")
        return suggestion

    except json.JSONDecodeError as e:
        logger.error(f"AI response JSON parse error: {e}\nRaw: {raw_response[:300]}")
        return {"error": "AI returned invalid response format. Please try again.", "raw": raw_response[:200]}
    except anthropic.AuthenticationError:
        return {"error": "Invalid ANTHROPIC_API_KEY. Please check your .env file."}
    except anthropic.RateLimitError:
        return {"error": "AI rate limit reached. Please wait a moment and try again."}
    except Exception as e:
        logger.error(f"AI advisor error: {e}")
        return {"error": f"AI analysis failed: {str(e)}"}
