"""
services/broker_angel.py — Angel One SmartAPI integration.

API docs: https://smartapi.angelbroking.com/docs
Apply at: https://smartapi.angelbroking.com

Angel One uses TOTP (Google Authenticator style) for 2FA.
You need: API Key, Client ID, MPIN, TOTP Secret
"""
from loguru import logger
from datetime import datetime
from typing import Optional
import pandas as pd

try:
    from SmartApi import SmartConnect
    import pyotp
    ANGEL_SDK_AVAILABLE = True
except ImportError:
    ANGEL_SDK_AVAILABLE = False
    logger.warning("smartapi-python not installed. Angel One will not work. Run: pip install smartapi-python")


class AngelOneService:
    """
    Wrapper around Angel One SmartAPI SDK.
    Handles login, orders, positions, and market data.
    """

    def __init__(self, api_key: str, client_id: str, mpin: str, totp_secret: str):
        if not ANGEL_SDK_AVAILABLE:
            raise ImportError("smartapi-python package required. Run: pip install smartapi-python")

        self.api_key     = api_key
        self.client_id   = client_id
        self.mpin        = mpin
        self.totp_secret = totp_secret
        self.smart       = SmartConnect(api_key=api_key)
        self._auth_token: Optional[str] = None
        self._feed_token: Optional[str] = None

    # ── Authentication ────────────────────────────────────────────────────────

    def login(self) -> dict:
        """
        Login to Angel One using MPIN + TOTP.
        Call once per day — access token is valid for 24 hours.
        """
        try:
            totp = pyotp.TOTP(self.totp_secret).now()
            data = self.smart.generateSession(
                clientCode=self.client_id,
                password=self.mpin,
                totp=totp,
            )

            if data["status"] is False:
                raise Exception(data["message"])

            self._auth_token = data["data"]["jwtToken"]
            self._feed_token = data["data"]["feedToken"]

            logger.info(f"✅ Angel One login successful: {self.client_id}")
            return {
                "access_token": self._auth_token,
                "feed_token":   self._feed_token,
                "client_id":    self.client_id,
                "broker":       "angel_one",
            }
        except Exception as e:
            logger.error(f"❌ Angel One login failed: {e}")
            raise

    def set_session(self, auth_token: str, feed_token: str):
        """Restore a saved session without re-logging in."""
        self._auth_token = auth_token
        self._feed_token = feed_token
        self.smart.setAccessToken(auth_token)

    def get_profile(self) -> dict:
        try:
            return self.smart.getProfile(self._feed_token)
        except Exception as e:
            return {"error": str(e)}

    # ── Funds & Positions ─────────────────────────────────────────────────────

    def get_funds(self) -> dict:
        try:
            data = self.smart.rmsLimit()
            rms = data.get("data", {})
            return {
                "available": float(rms.get("availablecash", 0)),
                "used":      float(rms.get("utiliseddebits", 0)),
                "total":     float(rms.get("net", 0)),
                "broker":    "angel_one",
            }
        except Exception as e:
            logger.error(f"Angel One funds error: {e}")
            return {"available": 0, "error": str(e)}

    def get_positions(self) -> list:
        try:
            data = self.smart.position()
            positions = data.get("data", []) or []
            return [
                {
                    "symbol":    p.get("tradingsymbol"),
                    "exchange":  p.get("exchange"),
                    "quantity":  int(p.get("netqty", 0)),
                    "buy_price": float(p.get("averageprice", 0)),
                    "ltp":       float(p.get("ltp", 0)),
                    "pnl":       float(p.get("pnl", 0)),
                    "broker":    "angel_one",
                }
                for p in positions if int(p.get("netqty", 0)) != 0
            ]
        except Exception as e:
            logger.error(f"Angel One positions error: {e}")
            return []

    # ── Order Management ──────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        exchange: str,
        action: str,           # BUY | SELL
        quantity: int,
        order_type: str = "MARKET",
        price: float = 0,
        trigger_price: float = 0,
        product: str = "INTRADAY",   # INTRADAY | DELIVERY | CARRYFORWARD
        token: str = "",
        tag: str = "HAWKER",
    ) -> dict:
        """Place a market or limit order via Angel One."""
        try:
            order_params = {
                "variety":          "NORMAL",
                "tradingsymbol":    symbol,
                "symboltoken":      token,
                "transactiontype":  action.upper(),
                "exchange":         exchange,
                "ordertype":        order_type,
                "producttype":      product,
                "duration":         "DAY",
                "price":            str(price) if order_type == "LIMIT" else "0",
                "squareoff":        "0",
                "stoploss":         str(trigger_price) if trigger_price else "0",
                "quantity":         str(quantity),
                "ordertag":         tag,
            }
            resp = self.smart.placeOrder(order_params)
            order_id = resp.get("data", {}).get("orderid", "")

            logger.info(f"✅ Angel One order placed: {action} {quantity} {symbol} → {order_id}")
            return {"order_id": str(order_id), "status": "placed", "broker": "angel_one"}

        except Exception as e:
            logger.error(f"❌ Angel One order error: {e}")
            return {"error": str(e), "status": "failed", "broker": "angel_one"}

    def cancel_order(self, order_id: str, variety: str = "NORMAL") -> dict:
        try:
            resp = self.smart.cancelOrder(order_id, variety)
            return {"order_id": order_id, "status": "cancelled"}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    def get_orders(self) -> list:
        try:
            data = self.smart.orderBook()
            return data.get("data", []) or []
        except Exception as e:
            logger.error(f"Angel One orders error: {e}")
            return []

    def get_ltp(self, exchange: str, symbol: str, token: str) -> float:
        """Get last traded price."""
        try:
            data = self.smart.ltpData(exchange, symbol, token)
            return float(data.get("data", {}).get("ltp", 0))
        except Exception:
            return 0.0
