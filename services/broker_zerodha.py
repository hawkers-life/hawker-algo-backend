"""
services/broker_zerodha.py — Zerodha Kite Connect integration.

API docs: https://kite.trade/docs/connect/v3/
Apply for API access: https://developers.kite.trade/

How Zerodha login works:
  1. User visits the Kite login URL
  2. After login, Kite redirects to your app with a request_token
  3. You exchange request_token for an access_token (valid 1 day)
  4. Use access_token for all trading operations
"""
from kiteconnect import KiteConnect, KiteTicker
from loguru import logger
from datetime import datetime
from typing import Optional
import pandas as pd


class ZerodhaService:
    """
    Wrapper around Zerodha's kiteconnect SDK.
    One instance per user session (holds their access_token).
    """

    def __init__(self, api_key: str, api_secret: str, access_token: Optional[str] = None):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.kite       = KiteConnect(api_key=api_key)
        if access_token:
            self.kite.set_access_token(access_token)

    # ── Authentication ────────────────────────────────────────────────────────

    def get_login_url(self) -> str:
        """Step 1: Get the URL to redirect user to for login."""
        return self.kite.login_url()

    def generate_session(self, request_token: str) -> dict:
        """
        Step 2: Exchange request_token for access_token.
        Call this once after the user is redirected back from Kite login.
        Returns: {"access_token": "...", "user_id": "...", "user_name": "..."}
        """
        try:
            data = self.kite.generate_session(request_token, api_secret=self.api_secret)
            self.kite.set_access_token(data["access_token"])
            logger.info(f"✅ Zerodha session generated for {data.get('user_id')}")
            return {
                "access_token": data["access_token"],
                "user_id":      data.get("user_id"),
                "user_name":    data.get("user_name"),
                "email":        data.get("email"),
                "broker":       "zerodha",
            }
        except Exception as e:
            logger.error(f"❌ Zerodha session error: {e}")
            raise

    def get_profile(self) -> dict:
        """Get user profile from Zerodha."""
        return self.kite.profile()

    # ── Account & Positions ───────────────────────────────────────────────────

    def get_funds(self) -> dict:
        """Get available margin/funds."""
        try:
            margins = self.kite.margins()
            equity = margins.get("equity", {})
            return {
                "available": equity.get("available", {}).get("live_balance", 0),
                "used":      equity.get("utilised", {}).get("debits", 0),
                "total":     equity.get("net", 0),
                "broker":    "zerodha",
            }
        except Exception as e:
            logger.error(f"Zerodha funds error: {e}")
            return {"available": 0, "error": str(e)}

    def get_positions(self) -> list:
        """Get current open positions."""
        try:
            positions = self.kite.positions()
            net = positions.get("net", [])
            return [
                {
                    "symbol":      p["tradingsymbol"],
                    "exchange":    p["exchange"],
                    "quantity":    p["quantity"],
                    "buy_price":   p["average_price"],
                    "ltp":         p["last_price"],
                    "pnl":         p["pnl"],
                    "broker":      "zerodha",
                }
                for p in net if p["quantity"] != 0
            ]
        except Exception as e:
            logger.error(f"Zerodha positions error: {e}")
            return []

    def get_holdings(self) -> list:
        """Get long-term holdings (delivery positions)."""
        try:
            return self.kite.holdings()
        except Exception as e:
            logger.error(f"Zerodha holdings error: {e}")
            return []

    # ── Order Management ──────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        exchange: str,
        action: str,            # BUY | SELL
        quantity: int,
        order_type: str = "MARKET",   # MARKET | LIMIT | SL | SL-M
        price: float = 0,
        trigger_price: float = 0,
        product: str = "MIS",         # MIS=intraday, CNC=delivery, NRML=F&O
        tag: str = "hawker_algo",
    ) -> dict:
        """Place an order on Zerodha."""
        try:
            transaction_type = self.kite.TRANSACTION_TYPE_BUY if action == "BUY" \
                               else self.kite.TRANSACTION_TYPE_SELL

            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=symbol,
                transaction_type=transaction_type,
                quantity=quantity,
                product=product,
                order_type=order_type,
                price=price if order_type == "LIMIT" else None,
                trigger_price=trigger_price if order_type in ("SL", "SL-M") else None,
                tag=tag,
            )
            logger.info(f"✅ Zerodha order placed: {action} {quantity} {symbol} → order_id={order_id}")
            return {"order_id": str(order_id), "status": "placed", "broker": "zerodha"}

        except Exception as e:
            logger.error(f"❌ Zerodha order error: {e}")
            return {"error": str(e), "status": "failed", "broker": "zerodha"}

    def cancel_order(self, order_id: str) -> dict:
        try:
            self.kite.cancel_order(variety=self.kite.VARIETY_REGULAR, order_id=order_id)
            return {"order_id": order_id, "status": "cancelled"}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    def get_orders(self) -> list:
        try:
            return self.kite.orders()
        except Exception as e:
            logger.error(f"Zerodha orders error: {e}")
            return []

    def get_order_status(self, order_id: str) -> dict:
        try:
            orders = self.kite.order_history(order_id=order_id)
            return orders[-1] if orders else {}
        except Exception as e:
            return {"error": str(e)}

    # ── Market Data ───────────────────────────────────────────────────────────

    def get_ltp(self, exchange: str, symbol: str) -> float:
        """Get last traded price via Kite (real-time, no delay)."""
        try:
            key = f"{exchange}:{symbol}"
            data = self.kite.ltp([key])
            return data[key]["last_price"]
        except Exception:
            return 0.0

    def get_historical_data(
        self, exchange: str, symbol: str,
        interval: str, from_date: str, to_date: str,
        instrument_token: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch intraday historical data via Kite API.
        interval: minute, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute, day
        Requires instrument_token — get via instruments API.
        """
        try:
            if not instrument_token:
                instruments = self.kite.instruments(exchange)
                match = next((i for i in instruments if i["tradingsymbol"] == symbol), None)
                if not match:
                    return pd.DataFrame()
                instrument_token = match["instrument_token"]

            data = self.kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
            )
            if not data:
                return pd.DataFrame()

            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                "close": "Close", "volume": "Volume"}, inplace=True)
            return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception as e:
            logger.error(f"Zerodha historical error: {e}")
            return pd.DataFrame()
