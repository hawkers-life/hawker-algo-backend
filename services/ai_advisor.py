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
from services.market_data import fetch_historical, fetch_live_quote, get_index_data
from config import get_settings

settings = get_settings()


# ── Pure pandas/numpy indicator helpers ──────────────────────────────────────

def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=length - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=length - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bbands(series: pd.Series, length: int = 20, std: float = 2.0):
    mid = series.rolling(window=length).mean()
    sigma = series.rolling(window=length).std()
    upper = mid + std * sigma
    lower = mid - std * sigma
    return upper, mid, lower


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()


def _vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    typical_price = (high + low + close) / 3
    return (typical_price * volume).cumsum() / volume.cumsum()


# ── Indicator summary ─────────────────────────────────────────────────────────

def _build_indicator_summary(df: pd.DataFrame) -> dict:
    """Calculate key technical indicators and return as dict for AI context."""
    if df.empty or len(df) < 30:
        return {}

    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]

    try:
        ema9  = _ema(close, 9)
        ema21 = _ema(close, 21)
        ema50 = _ema(close, 50)
        rsi14 = _rsi(close, 14)
        macd_line, macd_signal, macd_hist = _macd(close)
        bb_upper, bb_mid, bb_lower = _bbands(close, 20)
        atr   = _atr(high, low, close, 14)
        vwap  = _vwap(high, low, close, vol)

        last = -1

        ltp  = float(close.iloc[last])
        e9   = float(ema9.iloc[last])  if not pd.isna(ema9.iloc[last])  else None
        e21  = float(ema21.iloc[last]) if not pd.isna(ema21.iloc[last]) else None
        e50  = float(ema50.iloc[last]) if not pd.isna(ema50.iloc[last]) else None

        rsi_val   = float(rsi14.iloc[last])       if not pd.isna(rsi14.iloc[last])       else 50
        macd_val  = float(macd_line.iloc[last])   if not pd.isna(macd_line.iloc[last])   else 0
        macd_sig  = float(macd_signal.iloc[last]) if not pd.isna(macd_signal.iloc[last]) else 0
        macd_h    = float(macd_hist.iloc[last])   if not pd.isna(macd_hist.iloc[last])   else 0

        bb_u = float(bb_upper.iloc[last]) if not pd.isna(bb_upper.iloc[last]) else ltp * 1.02
        bb_l = float(bb_lower.iloc[last]) if not pd.isna(bb_lower.iloc[last]) else ltp * 0.98
        bb_m = float(bb_mid.iloc[last])   if not pd.isna(bb_mid.iloc[last])   else ltp

        atr_val  = float(atr.iloc[last])  if not pd.isna(atr.iloc[last])  else 0
        atr_pct  = (atr_val / ltp * 100)  if ltp > 0 else 0

        vwap_val = float(vwap.iloc[last]) if not pd.isna(vwap.iloc[last]) else ltp

        avg_vol_20 = float(vol.tail(20).mean())
        today_vol  = float(vol.iloc[last])
        vol_ratio  = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1

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

        change_5d  = ((ltp - float(close.iloc[-6]))  / float(close.iloc[-6])  * 100) if len(close) >= 6  else 0
        change_20d = ((ltp - float(close.iloc[-21])) / float(close.iloc[-21]) * 100) if len(close) >= 21 else 0

        return {
            "ltp":             round(ltp, 2),
            "ema9":            round(e9, 2)       if e9  else None,
            "ema21":           round(e21, 2)      if e21 else None,
            "ema50":           round(e50, 2)      if e50 else None,
            "rsi":             round(rsi_val, 1),
            "macd":            round(macd_val, 3),
            "macd_signal":     round(macd_sig, 3),
            "macd_histogram":  round(macd_h, 3),
            "bb_upper":        round(bb_u, 2),
            "bb_lower":        round(bb_l, 2),
            "bb_mid":          round(bb_m, 2),
            "atr":             round(atr_val, 2),
            "atr_pct":         round(atr_pct, 2),
            "vwap":            round(vwap_val, 2),
            "volume_ratio":    round(vol_ratio, 2),
            "trend":           trend,
            "price_vs_vwap":   "above" if ltp > vwap_val else "below",
            "price_vs_bb_mid": "above" if ltp > bb_m else "below",
            "change_5d_pct":   round(change_5d, 2),
            "change_20d_pct":  round(change_20d, 2),
        }
    except Exception as e:
        logger.error(f"Indicator calculation error: {e}")
        return {"ltp": float(close.iloc[-1]) if not close.empty else 0}


def _determine_market_regime(indicators: dict, index_data: dict) -> str:
    """Classify current market regime based on indicators."""
    nifty     = index_data.get("NIFTY 50", {})
    nifty_chg = nifty.get("change_pct", 0)
    rsi       = indicators.get("rsi", 50)
    trend     = indicators.get("trend", "neutral")
    vol_ratio = indicators.get("volume_ratio", 1)
    atr_pct   = indicators.get("atr_pct", 1)

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
    trading_style: str = "intraday",
    risk_tolerance: str = "moderate",
    capital: float = 100_000.0,
) -> dict:
    if not settings.ANTHROPIC_API_KEY:
        return {
            "error": "ANTHROPIC_API_KEY not configured. Please add it to your .env file.",
            "setup_guide": "Get your free API key at: https://console.anthropic.com"
        }

    logger.info(f"🤖 AI Advisor analysing {symbol} for {trading_style} trading...")

    df_daily  = fetch_historical(symbol, exchange, "1d", days=90)
    df_weekly = fetch_historical(symbol, exchange, "1wk", days=365)

    if df_daily.empty:
        return {"error": f"Could not fetch market data for {symbol}. Please check the symbol name."}

    daily_indicators  = _build_indicator_summary(df_daily)
    weekly_indicators = _build_indicator_summary(df_weekly)
    index_data        = get_index_data()
    live_quote        = fetch_live_quote(symbol, exchange)
    market_regime     = _determine_market_regime(daily_indicators, index_data)

    ltp = daily_indicators.get("ltp", live_quote.get("ltp", 0))

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

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        raw_response = message.content[0].text.strip()

        if raw_response.startswith("```"):
            raw_response = raw_response.split("```")[1]
            if raw_response.startswith("json"):
                raw_response = raw_response[4:]
        raw_response = raw_response.strip()

        suggestion = json.loads(raw_response)

        suggestion["_meta"] = {
            "generated_at":   datetime.now().isoformat(),
            "symbol":         symbol,
            "exchange":       exchange,
            "trading_style":  trading_style,
            "data_bars_used": len(df_daily),
            "model":          "claude-sonnet-4-20250514",
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
