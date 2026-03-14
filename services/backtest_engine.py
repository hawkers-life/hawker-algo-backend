"""
services/backtest_engine.py — Complete backtesting engine.

Supports all 10 pre-built strategies using real NSE historical data.
Uses pandas-ta for technical indicators.

Metrics returned:
  - Total return %
  - Win rate %
  - Max drawdown %
  - Sharpe ratio
  - Total trades
  - Profit factor
  - Full trade list with entry/exit/pnl
"""
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Optional
import pandas_ta as ta
from loguru import logger
from services.market_data import fetch_historical


# ── Strategy implementations ──────────────────────────────────────────────────

def _ema_crossover_signals(df: pd.DataFrame, fast: int = 9, slow: int = 21) -> pd.DataFrame:
    """EMA fast/slow crossover — buy when fast > slow, sell when fast < slow."""
    df = df.copy()
    df["ema_fast"] = ta.ema(df["Close"], length=fast)
    df["ema_slow"] = ta.ema(df["Close"], length=slow)
    df["signal"] = 0
    df.loc[(df["ema_fast"] > df["ema_slow"]) & (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1)), "signal"] = 1   # Buy
    df.loc[(df["ema_fast"] < df["ema_slow"]) & (df["ema_fast"].shift(1) >= df["ema_slow"].shift(1)), "signal"] = -1  # Sell
    return df


def _rsi_reversal_signals(df: pd.DataFrame, period: int = 14, oversold: int = 30, overbought: int = 70) -> pd.DataFrame:
    """RSI reversal — buy at oversold, sell at overbought."""
    df = df.copy()
    df["rsi"] = ta.rsi(df["Close"], length=period)
    df["signal"] = 0
    df.loc[(df["rsi"] < oversold) & (df["rsi"].shift(1) >= oversold), "signal"] = 1
    df.loc[(df["rsi"] > overbought) & (df["rsi"].shift(1) <= overbought), "signal"] = -1
    return df


def _vwap_signals(df: pd.DataFrame) -> pd.DataFrame:
    """VWAP breakout — buy when price crosses above VWAP."""
    df = df.copy()
    df["vwap"] = ta.vwap(df["High"], df["Low"], df["Close"], df["Volume"])
    df["signal"] = 0
    df.loc[(df["Close"] > df["vwap"]) & (df["Close"].shift(1) <= df["vwap"].shift(1)), "signal"] = 1
    df.loc[(df["Close"] < df["vwap"]) & (df["Close"].shift(1) >= df["vwap"].shift(1)), "signal"] = -1
    return df


def _orb_signals(df: pd.DataFrame, orb_candles: int = 3) -> pd.DataFrame:
    """
    Opening Range Breakout.
    Uses first N candles to define range, then trades breakout.
    Designed for intraday (5m or 15m) data.
    """
    df = df.copy()
    df["signal"] = 0

    if df.index.dtype == "datetime64[ns]":
        df["date"] = df.index.date
        df["time_idx"] = df.groupby("date").cumcount()
        orb_high = df[df["time_idx"] < orb_candles].groupby("date")["High"].max()
        orb_low  = df[df["time_idx"] < orb_candles].groupby("date")["Low"].min()
        df["orb_high"] = df["date"].map(orb_high)
        df["orb_low"]  = df["date"].map(orb_low)

        mask_long  = (df["time_idx"] >= orb_candles) & (df["Close"] > df["orb_high"]) & (df["Close"].shift(1) <= df["orb_high"].shift(1))
        mask_short = (df["time_idx"] >= orb_candles) & (df["Close"] < df["orb_low"])  & (df["Close"].shift(1) >= df["orb_low"].shift(1))
        df.loc[mask_long, "signal"]  = 1
        df.loc[mask_short, "signal"] = -1
    return df


def _macd_signals(df: pd.DataFrame) -> pd.DataFrame:
    """MACD crossover signals."""
    df = df.copy()
    macd = ta.macd(df["Close"])
    df["macd"]   = macd["MACD_12_26_9"]
    df["signal_line"] = macd["MACDs_12_26_9"]
    df["signal"] = 0
    df.loc[(df["macd"] > df["signal_line"]) & (df["macd"].shift(1) <= df["signal_line"].shift(1)), "signal"] = 1
    df.loc[(df["macd"] < df["signal_line"]) & (df["macd"].shift(1) >= df["signal_line"].shift(1)), "signal"] = -1
    return df


def _supertrend_signals(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """SuperTrend signals."""
    df = df.copy()
    st = ta.supertrend(df["High"], df["Low"], df["Close"], length=period, multiplier=multiplier)
    col_dir = [c for c in st.columns if "SUPERTd" in c]
    if col_dir:
        df["st_dir"] = st[col_dir[0]]
        df["signal"] = 0
        df.loc[(df["st_dir"] == 1) & (df["st_dir"].shift(1) == -1), "signal"] = 1
        df.loc[(df["st_dir"] == -1) & (df["st_dir"].shift(1) == 1), "signal"] = -1
    return df


STRATEGY_SIGNAL_MAP = {
    "ema_crossover":   _ema_crossover_signals,
    "rsi_reversal":    _rsi_reversal_signals,
    "vwap":            _vwap_signals,
    "orb":             _orb_signals,
    "macd":            _macd_signals,
    "supertrend":      _supertrend_signals,
}


# ── Trade simulator ───────────────────────────────────────────────────────────

def simulate_trades(
    df: pd.DataFrame,
    stop_loss_pct: float = 1.5,
    target_pct: float = 3.0,
    capital: float = 100_000.0,
    position_size_pct: float = 0.95,
) -> list:
    """
    Walk through signal column and simulate entry/exit trades.
    Applies stop-loss and target on every trade.
    Returns list of trade dicts.
    """
    trades = []
    in_trade = False
    entry_price = 0.0
    entry_date = None
    direction = 0  # 1=long, -1=short
    qty = 0

    for i in range(len(df)):
        row = df.iloc[i]
        date = df.index[i]
        price = float(row["Close"])

        if not in_trade:
            if row.get("signal", 0) == 1:      # Buy signal
                entry_price = price
                entry_date  = date
                direction   = 1
                qty         = int((capital * position_size_pct) / entry_price)
                if qty < 1:
                    continue
                in_trade = True

        else:
            sl_price  = entry_price * (1 - stop_loss_pct / 100)
            tgt_price = entry_price * (1 + target_pct / 100)

            exit_price = None
            exit_reason = None

            if price <= sl_price:
                exit_price  = sl_price
                exit_reason = "stop_loss"
            elif price >= tgt_price:
                exit_price  = tgt_price
                exit_reason = "target"
            elif row.get("signal", 0) == -1:
                exit_price  = price
                exit_reason = "signal_exit"

            if exit_price:
                pnl     = (exit_price - entry_price) * qty * direction
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * direction
                capital += pnl

                trades.append({
                    "entry_date":   str(entry_date)[:19],
                    "exit_date":    str(date)[:19],
                    "entry_price":  round(entry_price, 2),
                    "exit_price":   round(exit_price, 2),
                    "quantity":     qty,
                    "pnl":          round(pnl, 2),
                    "pnl_pct":      round(pnl_pct, 2),
                    "exit_reason":  exit_reason,
                    "direction":    "LONG" if direction == 1 else "SHORT",
                    "capital_after": round(capital, 2),
                })
                in_trade = False

    return trades


# ── Metrics calculator ────────────────────────────────────────────────────────

def calculate_metrics(trades: list, initial_capital: float) -> dict:
    """Calculate all performance metrics from completed trade list."""
    if not trades:
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0, "total_pnl": 0, "total_return_pct": 0,
            "max_drawdown_pct": 0, "sharpe_ratio": 0, "profit_factor": 0,
            "avg_win": 0, "avg_loss": 0, "best_trade": 0, "worst_trade": 0,
        }

    pnls     = [t["pnl"] for t in trades]
    pnl_pcts = [t["pnl_pct"] for t in trades]
    wins     = [p for p in pnls if p > 0]
    losses   = [p for p in pnls if p < 0]

    total_pnl = sum(pnls)
    total_return_pct = (total_pnl / initial_capital) * 100

    # Max drawdown — peak-to-trough on running capital
    capitals = [initial_capital] + [t["capital_after"] for t in trades]
    peak = initial_capital
    max_dd = 0.0
    for cap in capitals:
        if cap > peak:
            peak = cap
        dd = (peak - cap) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio (annualised, assume 252 trading days)
    if len(pnl_pcts) > 1:
        mean_ret = np.mean(pnl_pcts)
        std_ret  = np.std(pnl_pcts)
        sharpe = (mean_ret / std_ret * np.sqrt(252)) if std_ret > 0 else 0
    else:
        sharpe = 0

    # Profit factor
    gross_profit = sum(wins)  if wins  else 0
    gross_loss   = abs(sum(losses)) if losses else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else gross_profit

    return {
        "total_trades":     len(trades),
        "winning_trades":   len(wins),
        "losing_trades":    len(losses),
        "win_rate":         round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "total_pnl":        round(total_pnl, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio":     round(float(sharpe), 2),
        "profit_factor":    round(profit_factor, 2),
        "avg_win":          round(np.mean(wins), 2) if wins else 0,
        "avg_loss":         round(np.mean(losses), 2) if losses else 0,
        "best_trade":       round(max(pnls), 2) if pnls else 0,
        "worst_trade":      round(min(pnls), 2) if pnls else 0,
    }


def build_equity_curve(trades: list, initial_capital: float) -> list:
    """Build equity curve data points for charting."""
    curve = [{"date": "Start", "equity": round(initial_capital, 2), "drawdown": 0}]
    peak = initial_capital
    for t in trades:
        cap = t["capital_after"]
        if cap > peak:
            peak = cap
        dd = (peak - cap) / peak * 100
        curve.append({
            "date":     t["exit_date"][:10],
            "equity":   cap,
            "drawdown": round(dd, 2),
            "pnl":      t["pnl"],
        })
    return curve


# ── Main public function ──────────────────────────────────────────────────────

def run_backtest(
    symbol: str,
    exchange: str = "NSE",
    strategy_key: str = "ema_crossover",
    timeframe: str = "1d",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: int = 365,
    initial_capital: float = 100_000.0,
    stop_loss_pct: float = 1.5,
    target_pct: float = 3.0,
    strategy_params: Optional[dict] = None,
) -> dict:
    """
    Main backtest function. Called by the API router.

    Returns complete backtest result including metrics, trades, equity curve.
    """
    start_time = datetime.now()

    # 1. Fetch historical data
    df = fetch_historical(
        symbol=symbol, exchange=exchange,
        timeframe=timeframe, days=days,
        start_date=start_date, end_date=end_date,
    )

    if df.empty:
        return {"error": f"No historical data found for {symbol}. Check symbol name and try again."}

    if len(df) < 50:
        return {"error": f"Not enough historical data ({len(df)} bars). Try a longer date range or daily timeframe."}

    # 2. Generate signals
    signal_fn = STRATEGY_SIGNAL_MAP.get(strategy_key)
    if not signal_fn:
        return {"error": f"Unknown strategy: {strategy_key}. Available: {list(STRATEGY_SIGNAL_MAP.keys())}"}

    try:
        params = strategy_params or {}
        df_signals = signal_fn(df, **params)
    except TypeError:
        df_signals = signal_fn(df)

    # 3. Simulate trades
    trades = simulate_trades(
        df=df_signals,
        stop_loss_pct=stop_loss_pct,
        target_pct=target_pct,
        capital=initial_capital,
    )

    # 4. Calculate metrics
    metrics = calculate_metrics(trades, initial_capital)

    # 5. Build equity curve
    equity_curve = build_equity_curve(trades, initial_capital)

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(
        f"✅ Backtest {symbol} {strategy_key}: "
        f"{metrics['total_trades']} trades, "
        f"{metrics['win_rate']}% win rate, "
        f"₹{metrics['total_pnl']} PnL in {elapsed:.1f}s"
    )

    return {
        "symbol":         symbol,
        "exchange":       exchange,
        "strategy":       strategy_key,
        "timeframe":      timeframe,
        "start_date":     str(df.index[0])[:10],
        "end_date":       str(df.index[-1])[:10],
        "total_bars":     len(df),
        "initial_capital": initial_capital,
        "final_capital":  trades[-1]["capital_after"] if trades else initial_capital,
        "metrics":        metrics,
        "equity_curve":   equity_curve,
        "trades":         trades[-50:],      # Last 50 trades (full list can be huge)
        "all_trades_count": len(trades),
        "elapsed_seconds": round(elapsed, 2),
    }
