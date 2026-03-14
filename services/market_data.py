"""
services/market_data.py — Market data fetcher for NSE/BSE.

Uses yfinance (Yahoo Finance) — FREE, no API key needed.
NSE symbols: append .NS  → e.g. RELIANCE.NS, TCS.NS
BSE symbols: append .BO  → e.g. RELIANCE.BO
Indices:  ^NSEI (NIFTY 50), ^NSEBANK (BankNifty), ^BSESN (SENSEX)

For F&O expiry/chain data we use NSE India's public endpoints.
"""
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger
import requests
import time

# ── Symbol helpers ────────────────────────────────────────────────────────────

NSE_SUFFIX = ".NS"
BSE_SUFFIX = ".BO"

INDEX_MAP = {
    "NIFTY":     "^NSEI",
    "NIFTY50":   "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX":    "^BSESN",
    "NIFTYMID":  "^NSEMDCP50",
    "FINNIFTY":  "NIFTY_FIN_SERVICE.NS",
}

TIMEFRAME_MAP = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "60m",
    "1d":  "1d",
    "1wk": "1wk",
}

# yfinance period limits per interval
INTERVAL_MAX_DAYS = {
    "1m":  7,
    "5m":  60,
    "15m": 60,
    "30m": 60,
    "60m": 730,
    "1d":  3650,
    "1wk": 3650,
}


def resolve_symbol(symbol: str, exchange: str = "NSE") -> str:
    """Convert trading symbol to Yahoo Finance format."""
    sym = symbol.upper().strip()

    # Already in YF format
    if sym.startswith("^") or sym.endswith(".NS") or sym.endswith(".BO"):
        return sym

    # Index mapping
    if sym in INDEX_MAP:
        return INDEX_MAP[sym]

    # Exchange-based suffix
    suffix = NSE_SUFFIX if exchange.upper() in ("NSE", "NFO") else BSE_SUFFIX
    return f"{sym}{suffix}"


# ── Core data fetcher ─────────────────────────────────────────────────────────

def fetch_historical(
    symbol: str,
    exchange: str = "NSE",
    timeframe: str = "1d",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: int = 365,
) -> pd.DataFrame:
    """
    Fetch OHLCV historical data for any NSE/BSE symbol.

    Returns DataFrame with columns: Open, High, Low, Close, Volume
    Index: DatetimeIndex (timezone-aware, IST)
    """
    yf_symbol = resolve_symbol(symbol, exchange)
    interval = TIMEFRAME_MAP.get(timeframe, "1d")

    # Calculate date range
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if not start_date:
        max_days = INTERVAL_MAX_DAYS.get(interval, 365)
        actual_days = min(days, max_days)
        start_dt = datetime.now() - timedelta(days=actual_days)
        start_date = start_dt.strftime("%Y-%m-%d")

    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(
            start=start_date,
            end=end_date,
            interval=interval,
            auto_adjust=True,
            prepost=False,
        )

        if df.empty:
            logger.warning(f"No data returned for {yf_symbol} ({interval})")
            return pd.DataFrame()

        # Standardise column names
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index.name = "Date"

        # Remove timezone for easier handling
        if df.index.tzinfo is not None:
            df.index = df.index.tz_localize(None)

        logger.info(f"✅ Fetched {len(df)} bars for {yf_symbol} @ {interval}")
        return df

    except Exception as e:
        logger.error(f"❌ fetch_historical({yf_symbol}): {e}")
        return pd.DataFrame()


def fetch_live_quote(symbol: str, exchange: str = "NSE") -> dict:
    """
    Fetch latest quote (LTP, day high/low, change%) for a symbol.
    Uses yfinance fast_info — near real-time (15-min delayed for NSE).
    """
    yf_symbol = resolve_symbol(symbol, exchange)
    try:
        ticker = yf.Ticker(yf_symbol)
        info = ticker.fast_info

        return {
            "symbol": symbol,
            "yf_symbol": yf_symbol,
            "ltp": round(float(info.last_price or 0), 2),
            "open": round(float(info.open or 0), 2),
            "day_high": round(float(info.day_high or 0), 2),
            "day_low": round(float(info.day_low or 0), 2),
            "prev_close": round(float(info.previous_close or 0), 2),
            "volume": int(info.three_month_average_volume or 0),
            "market_cap": info.market_cap,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"❌ fetch_live_quote({yf_symbol}): {e}")
        return {"symbol": symbol, "ltp": 0, "error": str(e)}


def fetch_multiple_quotes(symbols: list, exchange: str = "NSE") -> list:
    """Fetch quotes for multiple symbols in one call (faster)."""
    yf_symbols = [resolve_symbol(s, exchange) for s in symbols]
    try:
        data = yf.download(
            tickers=yf_symbols,
            period="2d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
        )
        results = []
        for sym, yf_sym in zip(symbols, yf_symbols):
            try:
                if len(yf_symbols) == 1:
                    last = data["Close"].iloc[-1]
                    prev = data["Close"].iloc[-2]
                else:
                    last = data[yf_sym]["Close"].iloc[-1]
                    prev = data[yf_sym]["Close"].iloc[-2]
                change_pct = ((last - prev) / prev) * 100
                results.append({
                    "symbol": sym,
                    "ltp": round(float(last), 2),
                    "change_pct": round(float(change_pct), 2),
                })
            except Exception:
                results.append({"symbol": sym, "ltp": 0, "change_pct": 0})
        return results
    except Exception as e:
        logger.error(f"❌ fetch_multiple_quotes: {e}")
        return [{"symbol": s, "ltp": 0, "change_pct": 0} for s in symbols]


def get_index_data() -> dict:
    """Fetch live NIFTY, BankNifty, Sensex for dashboard ticker."""
    indices = {
        "NIFTY 50":    "^NSEI",
        "BANKNIFTY":   "^NSEBANK",
        "SENSEX":      "^BSESN",
    }
    result = {}
    for name, sym in indices.items():
        try:
            ticker = yf.Ticker(sym)
            info = ticker.fast_info
            ltp = float(info.last_price or 0)
            prev = float(info.previous_close or ltp)
            change = ltp - prev
            change_pct = (change / prev * 100) if prev else 0
            result[name] = {
                "ltp": round(ltp, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "up": change >= 0,
            }
        except Exception:
            result[name] = {"ltp": 0, "change": 0, "change_pct": 0, "up": True}
    return result


def search_symbols(query: str, exchange: str = "NSE") -> list:
    """
    Search for symbols matching query using NSE's public API.
    Falls back to a hardcoded list if NSE API is unavailable.
    """
    # Try NSE public search API
    try:
        url = f"https://www.nseindia.com/api/search/autocomplete?q={query}"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com",
        }
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.ok:
            data = resp.json()
            symbols = data.get("symbols", [])
            return [
                {"symbol": s.get("symbol", ""), "name": s.get("symbol_info", ""), "type": s.get("result_type", "")}
                for s in symbols[:10]
            ]
    except Exception:
        pass

    # Fallback: popular symbols list
    popular = [
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
        "SBIN", "WIPRO", "HCLTECH", "LT", "KOTAKBANK",
        "AXISBANK", "BAJFINANCE", "MARUTI", "TITAN", "NESTLEIND",
        "NIFTY", "BANKNIFTY", "SENSEX",
    ]
    query_upper = query.upper()
    matches = [s for s in popular if query_upper in s]
    return [{"symbol": s, "name": s, "type": "EQ"} for s in matches[:10]]
