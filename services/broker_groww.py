"""
services/broker_groww.py — Groww API integration (PRIMARY BROKER).

Apply at: https://groww.in/stocks/developer-api
Groww uses OAuth2 for authentication.

Flow:
  1. User clicks "Connect Groww" — we redirect to Groww OAuth
  2. User authorises → Groww sends back auth_code to our callback URL
  3. We exchange auth_code for access_token
  4. Use access_token for all API calls

⚠️  Groww's trading API is in beta/invite-only as of 2025.
    This implementation follows their documented REST API spec.
    If endpoints change, only THIS FILE needs updating.

API Base: https://api.groww.in/v1
"""
import requests
import json
from loguru import logger
from datetime import datetime, timedelta
from typing import Optional
import urllib.parse


GROWW_BASE_URL   = "https://api.groww.in/v1"
GROWW_AUTH_URL   = "https://groww.in/v1/oauth/authorize"
GROWW_TOKEN_URL  = "https://api.groww.in/v1/oauth/token"

# Groww exchange codes
EXCHANGE_NSE = "NSE"
EXCHANGE_BSE = "BSE"
EXCHANGE_NFO = "NFO"   # F&O

# Groww product types
PRODUCT_INTRADAY = "INTRADAY"   # MIS equivalent
PRODUCT_DELIVERY = "DELIVERY"   # CNC equivalent
PRODUCT_MARGIN   = "MARGIN"     # NRML equivalent


class GrowwService:
    """
    Groww API client — primary broker for Hawker Algo.
    Handles auth, orders, positions, portfolio, and market data.
    """

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, access_token: Optional[str] = None):
        self.client_id     = client_id
        self.client_secret = client_secret
        self.redirect_uri  = redirect_uri
        self.access_token  = access_token
        self._session      = requests.Session()
        if access_token:
            self._set_auth_header(access_token)

    def _set_auth_header(self, token: str):
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "X-Source":      "api",
        })

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{GROWW_BASE_URL}{path}"
        try:
            resp = self._session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Groww GET {path} HTTP error: {e.response.status_code} {e.response.text[:200]}")
            raise
        except Exception as e:
            logger.error(f"Groww GET {path} error: {e}")
            raise

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{GROWW_BASE_URL}{path}"
        try:
            resp = self._session.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Groww POST {path} HTTP error: {e.response.status_code} {e.response.text[:200]}")
            raise
        except Exception as e:
            logger.error(f"Groww POST {path} error: {e}")
            raise

    def _delete(self, path: str, params: dict = None) -> dict:
        url = f"{GROWW_BASE_URL}{path}"
        try:
            resp = self._session.delete(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Groww DELETE {path} error: {e}")
            raise

    # ── OAuth2 Authentication ─────────────────────────────────────────────────

    def get_login_url(self, state: str = "hawker_algo") -> str:
        """
        Step 1: Build Groww OAuth URL to show user.
        state: random string to prevent CSRF (store in session).
        """
        params = {
            "client_id":     self.client_id,
            "redirect_uri":  self.redirect_uri,
            "response_type": "code",
            "scope":         "orders holdings positions profile",
            "state":         state,
        }
        url = f"{GROWW_AUTH_URL}?{urllib.parse.urlencode(params)}"
        logger.info(f"Groww login URL generated for client {self.client_id}")
        return url

    def exchange_code_for_token(self, auth_code: str) -> dict:
        """
        Step 2: Exchange the auth_code for access_token.
        Call this when Groww redirects back to your callback URL.
        """
        try:
            payload = {
                "grant_type":    "authorization_code",
                "client_id":     self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri":  self.redirect_uri,
                "code":          auth_code,
            }
            resp = requests.post(GROWW_TOKEN_URL, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            token         = data.get("access_token")
            refresh_token = data.get("refresh_token")
            expires_in    = data.get("expires_in", 86400)
            expires_at    = (datetime.now() + timedelta(seconds=expires_in)).isoformat()

            self.access_token = token
            self._set_auth_header(token)

            logger.info(f"✅ Groww access token obtained (expires in {expires_in}s)")
            return {
                "access_token":  token,
                "refresh_token": refresh_token,
                "expires_at":    expires_at,
                "broker":        "groww",
            }
        except Exception as e:
            logger.error(f"❌ Groww token exchange failed: {e}")
            raise

    def refresh_access_token(self, refresh_token: str) -> dict:
        """Refresh an expired access token using refresh token."""
        try:
            payload = {
                "grant_type":    "refresh_token",
                "client_id":     self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
            }
            resp = requests.post(GROWW_TOKEN_URL, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self.access_token = data["access_token"]
            self._set_auth_header(self.access_token)
            logger.info("✅ Groww token refreshed")
            return data
        except Exception as e:
            logger.error(f"❌ Groww token refresh failed: {e}")
            raise

    # ── User Profile & Funds ──────────────────────────────────────────────────

    def get_profile(self) -> dict:
        """Get user profile information."""
        try:
            data = self._get("/user/profile")
            return {
                "name":      data.get("name"),
                "email":     data.get("email"),
                "client_id": data.get("clientCode") or data.get("userId"),
                "pan":       data.get("pan"),
                "broker":    "groww",
            }
        except Exception as e:
            return {"error": str(e), "broker": "groww"}

    def get_funds(self) -> dict:
        """Get available balance and margin."""
        try:
            data = self._get("/user/trading-info")
            return {
                "available": float(data.get("availableBalance", 0)),
                "used":      float(data.get("usedMargin", 0)),
                "total":     float(data.get("totalBalance", 0)),
                "broker":    "groww",
            }
        except Exception as e:
            logger.error(f"Groww funds error: {e}")
            return {"available": 0, "error": str(e), "broker": "groww"}

    # ── Portfolio & Positions ─────────────────────────────────────────────────

    def get_positions(self) -> list:
        """Get current intraday positions."""
        try:
            data = self._get("/portfolio/positions")
            positions = data.get("positions", []) or []
            return [
                {
                    "symbol":    p.get("tradingSymbol"),
                    "exchange":  p.get("exchange"),
                    "quantity":  int(p.get("quantity", 0)),
                    "buy_price": float(p.get("averagePrice", 0)),
                    "ltp":       float(p.get("ltp", 0)),
                    "pnl":       float(p.get("realisedPnl", 0)) + float(p.get("unrealisedPnl", 0)),
                    "product":   p.get("product"),
                    "broker":    "groww",
                }
                for p in positions if int(p.get("quantity", 0)) != 0
            ]
        except Exception as e:
            logger.error(f"Groww positions error: {e}")
            return []

    def get_holdings(self) -> list:
        """Get long-term delivery holdings."""
        try:
            data = self._get("/portfolio/holdings")
            holdings = data.get("holdings", []) or []
            return [
                {
                    "symbol":    h.get("tradingSymbol"),
                    "exchange":  h.get("exchange"),
                    "quantity":  int(h.get("holdingQuantity", 0)),
                    "avg_price": float(h.get("averagePrice", 0)),
                    "ltp":       float(h.get("ltp", 0)),
                    "pnl":       float(h.get("pnl", 0)),
                    "pnl_pct":   float(h.get("dayChangePct", 0)),
                    "broker":    "groww",
                }
                for h in holdings
            ]
        except Exception as e:
            logger.error(f"Groww holdings error: {e}")
            return []

    # ── Order Management ──────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        exchange: str,
        action: str,               # BUY | SELL
        quantity: int,
        order_type: str = "MARKET",  # MARKET | LIMIT | SL | SL-M
        price: float = 0.0,
        trigger_price: float = 0.0,
        product: str = PRODUCT_INTRADAY,
        validity: str = "DAY",
        tag: str = "HAWKER_ALGO",
    ) -> dict:
        """
        Place an order on Groww.
        Returns dict with order_id on success, error string on failure.
        """
        payload = {
            "tradingSymbol": symbol.upper(),
            "exchange":      exchange.upper(),
            "transactionType": action.upper(),
            "orderType":     order_type.upper(),
            "product":       product,
            "quantity":      quantity,
            "price":         price if order_type in ("LIMIT", "SL") else 0,
            "triggerPrice":  trigger_price if order_type in ("SL", "SL-M") else 0,
            "validity":      validity,
            "tag":           tag,
        }

        try:
            resp = self._post("/orders/regular", payload)
            order_id = resp.get("orderId") or resp.get("order_id") or resp.get("data", {}).get("orderId", "")
            logger.info(f"✅ Groww order placed: {action} {quantity}×{symbol} @ {order_type} → {order_id}")
            return {
                "order_id": str(order_id),
                "status":   "placed",
                "broker":   "groww",
                "symbol":   symbol,
                "action":   action,
                "quantity": quantity,
            }
        except Exception as e:
            logger.error(f"❌ Groww place_order error: {e}")
            return {"error": str(e), "status": "failed", "broker": "groww"}

    def modify_order(
        self,
        order_id: str,
        price: float = 0,
        quantity: int = 0,
        order_type: str = "LIMIT",
        trigger_price: float = 0,
    ) -> dict:
        """Modify a pending order (change price/quantity)."""
        payload = {
            "orderId":      order_id,
            "orderType":    order_type,
            "price":        price,
            "quantity":     quantity,
            "triggerPrice": trigger_price,
        }
        try:
            resp = self._post(f"/orders/{order_id}/modify", payload)
            logger.info(f"✅ Groww order modified: {order_id}")
            return {"order_id": order_id, "status": "modified"}
        except Exception as e:
            logger.error(f"Groww modify_order error: {e}")
            return {"error": str(e), "status": "failed"}

    def cancel_order(self, order_id: str) -> dict:
        """Cancel a pending order."""
        try:
            resp = self._delete(f"/orders/{order_id}")
            logger.info(f"✅ Groww order cancelled: {order_id}")
            return {"order_id": order_id, "status": "cancelled"}
        except Exception as e:
            logger.error(f"Groww cancel_order error: {e}")
            return {"error": str(e), "status": "failed"}

    def get_orders(self) -> list:
        """Get all orders for today."""
        try:
            data = self._get("/orders")
            return data.get("orders", []) or []
        except Exception as e:
            logger.error(f"Groww get_orders error: {e}")
            return []

    def get_order_detail(self, order_id: str) -> dict:
        """Get status and details of a specific order."""
        try:
            return self._get(f"/orders/{order_id}")
        except Exception as e:
            return {"error": str(e), "order_id": order_id}

    def get_trade_book(self) -> list:
        """Get executed trades for today."""
        try:
            data = self._get("/orders/trades")
            return data.get("trades", []) or []
        except Exception as e:
            logger.error(f"Groww trade book error: {e}")
            return []

    # ── Market Data ───────────────────────────────────────────────────────────

    def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        """Get last traded price for a symbol."""
        try:
            data = self._get("/market/ltp", {"symbol": symbol, "exchange": exchange})
            return float(data.get("ltp", 0) or data.get("data", {}).get("ltp", 0))
        except Exception as e:
            logger.warning(f"Groww LTP error for {symbol}: {e}")
            return 0.0

    def search_symbols(self, query: str) -> list:
        """Search for instruments on Groww."""
        try:
            data = self._get("/search/instruments", {"query": query})
            return data.get("data", []) or []
        except Exception as e:
            logger.error(f"Groww symbol search error: {e}")
            return []

    def get_instrument_info(self, symbol: str, exchange: str = "NSE") -> dict:
        """Get full instrument details including lot size for F&O."""
        try:
            return self._get(f"/instruments/{exchange}/{symbol}")
        except Exception as e:
            return {"error": str(e)}

    # ── Convenience: Place with Stop Loss + Target ────────────────────────────

    def place_bracket_order(
        self,
        symbol: str,
        exchange: str,
        action: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
        target_price: float,
        product: str = PRODUCT_INTRADAY,
    ) -> dict:
        """
        Place entry order + stoploss order in sequence.
        Groww does not have native bracket orders — we simulate with 2 orders.
        Entry order + an immediately placed SL order.
        """
        # Place entry order
        entry_result = self.place_order(
            symbol=symbol, exchange=exchange, action=action,
            quantity=quantity, order_type="LIMIT", price=entry_price, product=product,
        )
        if entry_result.get("status") != "placed":
            return {"error": "Entry order failed", "details": entry_result}

        # Place SL order (opposite side)
        sl_action = "SELL" if action == "BUY" else "BUY"
        sl_result = self.place_order(
            symbol=symbol, exchange=exchange, action=sl_action,
            quantity=quantity, order_type="SL-M",
            trigger_price=stop_loss_price, product=product,
        )

        return {
            "entry_order":    entry_result,
            "stoploss_order": sl_result,
            "symbol":         symbol,
            "action":         action,
            "quantity":       quantity,
            "status":         "bracket_placed",
        }
