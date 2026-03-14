"""
services/order_manager.py — Unified order execution engine.

Every live order goes through here. Responsibilities:
  1. Pre-trade risk checks (daily loss, position size, halted?)
  2. Route to correct broker service
  3. Save trade record to database
  4. Update strategy metrics
  5. Trigger notifications (Telegram/email)
  6. Post-trade risk monitoring
"""
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from loguru import logger
from models.trade import Trade, TradeAction, TradeStatus, TradeMode
from models.strategy import Strategy
from models.subscription import RiskConfig
from services.broker_factory import get_broker_service
from models.strategy import BrokerAccount


# ── Risk pre-check ────────────────────────────────────────────────────────────

class RiskViolation(Exception):
    """Raised when an order violates risk rules — order is blocked."""
    pass


def _pre_trade_risk_check(
    user_id: str,
    symbol: str,
    quantity: int,
    price: float,
    db: Session,
) -> None:
    """
    Check all risk rules before sending order to broker.
    Raises RiskViolation if any rule is breached.
    """
    config = db.query(RiskConfig).filter(RiskConfig.user_id == user_id).first()
    if not config:
        return  # No config — allow (default behaviour)

    # 1. Is trading halted?
    if config.is_trading_halted:
        raise RiskViolation(f"Trading is halted: {config.halt_reason or 'Emergency stop active'}")

    # 2. Max position size check
    order_value = quantity * price
    if config.max_position_size and order_value > config.max_position_size:
        raise RiskViolation(
            f"Order value ₹{order_value:,.0f} exceeds max position size ₹{config.max_position_size:,.0f}"
        )

    # 3. Daily loss limit check
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    todays_pnl = db.query(func.coalesce(func.sum(Trade.pnl), 0.0)).filter(
        and_(
            Trade.user_id == user_id,
            Trade.placed_at >= today_start,
            Trade.status.in_([TradeStatus.FILLED, TradeStatus.SQUARED_OFF])
        )
    ).scalar()

    if todays_pnl < 0 and abs(float(todays_pnl)) >= config.max_daily_loss:
        # Auto-halt trading
        config.is_trading_halted = True
        config.halt_reason = f"Daily loss limit ₹{config.max_daily_loss:,.0f} reached"
        db.commit()
        raise RiskViolation(f"Daily loss limit reached. Trading halted automatically.")


# ── Main execution function ───────────────────────────────────────────────────

def execute_order(
    user_id: str,
    strategy_id: str,
    symbol: str,
    exchange: str,
    action: str,               # BUY | SELL
    quantity: int,
    order_type: str = "MARKET",
    price: float = 0.0,
    stop_loss_price: float = 0.0,
    target_price: float = 0.0,
    mode: str = "paper",        # paper | live | forward_test
    broker_name: str = "groww",
    db: Session = None,
) -> dict:
    """
    Unified order execution entry point.

    For paper mode: simulates the order and records it with current market price.
    For live mode:  sends real order to broker via the broker service.
    """
    try:
        # ── 1. Risk check (all modes) ─────────────────────────────────────────
        _pre_trade_risk_check(user_id, symbol, quantity, price or 1, db)

        # ── 2. Route by mode ──────────────────────────────────────────────────
        broker_order_id = None

        if mode == "live":
            # Get broker account from DB
            broker_account = db.query(BrokerAccount).filter(
                and_(
                    BrokerAccount.user_id == user_id,
                    BrokerAccount.broker == broker_name,
                    BrokerAccount.is_active == True,
                    BrokerAccount.is_connected == True,
                )
            ).first()

            if not broker_account:
                raise RiskViolation(f"No connected {broker_name} account found. Please connect your broker first.")

            broker_svc = get_broker_service(broker_name, broker_account)

            result = broker_svc.place_order(
                symbol=symbol, exchange=exchange,
                action=action, quantity=quantity,
                order_type=order_type, price=price,
                trigger_price=stop_loss_price,
            )
            if result.get("status") != "placed":
                raise Exception(result.get("error", "Broker rejected the order"))

            broker_order_id = result.get("order_id")
            logger.info(f"✅ Live order sent to {broker_name}: {action} {quantity}×{symbol} → broker_id={broker_order_id}")

        elif mode == "paper":
            # Simulate — mark as filled immediately at last known price
            broker_order_id = f"PAPER-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            logger.info(f"📋 Paper order simulated: {action} {quantity}×{symbol}")

        elif mode == "forward_test":
            broker_order_id = f"FWD-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            logger.info(f"🔭 Forward test order: {action} {quantity}×{symbol}")

        # ── 3. Record trade in database ───────────────────────────────────────
        trade = Trade(
            user_id         = user_id,
            strategy_id     = strategy_id or None,
            symbol          = symbol.upper(),
            exchange        = exchange.upper(),
            action          = TradeAction.BUY if action == "BUY" else TradeAction.SELL,
            quantity        = quantity,
            order_type      = order_type,
            entry_price     = price or None,
            stop_loss_price = stop_loss_price or None,
            target_price    = target_price or None,
            status          = TradeStatus.OPEN if mode == "live" else TradeStatus.FILLED,
            mode            = TradeMode.LIVE if mode == "live" else (
                              TradeMode.FORWARD_TEST if mode == "forward_test" else TradeMode.PAPER),
            broker_order_id = broker_order_id,
            placed_at       = datetime.now(timezone.utc),
            filled_at       = datetime.now(timezone.utc) if mode in ("paper", "forward_test") else None,
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)

        # ── 4. Update strategy trade count ────────────────────────────────────
        if strategy_id:
            strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
            if strategy:
                strategy.total_trades += 1
                strategy.last_trade_at = datetime.now(timezone.utc)
                db.commit()

        return {
            "trade_id":       str(trade.id),
            "broker_order_id": broker_order_id,
            "symbol":         symbol,
            "action":         action,
            "quantity":       quantity,
            "mode":           mode,
            "status":         trade.status,
            "placed_at":      trade.placed_at.isoformat(),
        }

    except RiskViolation as rv:
        logger.warning(f"⚠️ Risk violation blocked order: {rv}")
        raise
    except Exception as e:
        logger.error(f"❌ execute_order failed: {e}")
        raise


def close_trade(trade_id: str, exit_price: float, db: Session) -> dict:
    """Mark an open trade as closed (squared off)."""
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        return {"error": "Trade not found"}
    if trade.entry_price and exit_price:
        direction = 1 if trade.action == TradeAction.BUY else -1
        pnl = (exit_price - trade.entry_price) * trade.quantity * direction
        trade.pnl     = round(pnl, 2)
        trade.pnl_pct = round((pnl / (trade.entry_price * trade.quantity)) * 100, 2)
        trade.net_pnl = trade.pnl  # brokerage deduction can be added
    trade.exit_price = exit_price
    trade.status     = TradeStatus.SQUARED_OFF
    trade.closed_at  = datetime.now(timezone.utc)
    db.commit()

    # Update strategy P&L
    if trade.strategy_id:
        strategy = db.query(Strategy).filter(Strategy.id == trade.strategy_id).first()
        if strategy:
            strategy.total_pnl += trade.pnl or 0
            if trade.pnl and trade.pnl > 0:
                strategy.winning_trades += 1
            strategy.win_rate = (
                (strategy.winning_trades / strategy.total_trades * 100)
                if strategy.total_trades > 0 else 0
            )
            db.commit()

    return {"trade_id": trade_id, "pnl": trade.pnl, "status": "squared_off"}
