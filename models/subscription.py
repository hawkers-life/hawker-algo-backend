"""
models/subscription.py — Subscription and payment records.
"""
from sqlalchemy import Column, String, DateTime, Enum, Float, ForeignKey, Boolean, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
import enum
from database import Base


class PlanType(str, enum.Enum):
    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    ELITE = "elite"


class BillingCycle(str, enum.Enum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    REFUNDED = "refunded"


# Plan limits — what each plan allows
PLAN_LIMITS = {
    "free": {
        "max_strategies": 1,
        "live_trading": False,
        "max_capital": 0,
        "backtesting": True,
        "paper_trading": True,
        "brokers": 1,
    },
    "basic": {
        "max_strategies": 3,
        "live_trading": True,
        "max_capital": 500000,
        "backtesting": True,
        "paper_trading": True,
        "brokers": 2,
    },
    "pro": {
        "max_strategies": 7,
        "live_trading": True,
        "max_capital": 2000000,
        "backtesting": True,
        "paper_trading": True,
        "brokers": 4,
    },
    "elite": {
        "max_strategies": 10,
        "live_trading": True,
        "max_capital": -1,  # unlimited
        "backtesting": True,
        "paper_trading": True,
        "brokers": 4,
    },
}

# Pricing in INR
PLAN_PRICING = {
    "free": {"monthly": 0, "annual": 0},
    "basic": {"monthly": 999, "annual": 9999},
    "pro": {"monthly": 2499, "annual": 24999},
    "elite": {"monthly": 4999, "annual": 49999},
}


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    plan = Column(Enum(PlanType), nullable=False)
    billing_cycle = Column(Enum(BillingCycle), default=BillingCycle.MONTHLY)
    amount = Column(Float, nullable=False)

    starts_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Boolean, default=True)
    auto_renew = Column(Boolean, default=True)

    # Cashfree payment tracking
    cashfree_order_id = Column(String(100), nullable=True)
    cashfree_payment_id = Column(String(100), nullable=True)
    payment_status = Column(Enum(PaymentStatus), default=PaymentStatus.PENDING)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RiskConfig(Base):
    """Per-user global risk settings."""
    __tablename__ = "risk_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)

    # Kill switches
    max_daily_loss = Column(Float, default=5000.0)        # Absolute INR
    max_daily_loss_pct = Column(Float, default=2.0)       # % of capital
    max_drawdown_pct = Column(Float, default=10.0)        # Circuit breaker
    max_capital_deployed_pct = Column(Float, default=80.0) # Max % of capital in market

    # Per-trade limits
    max_position_size = Column(Float, default=50000.0)    # Max capital per trade INR
    default_stop_loss_pct = Column(Float, default=1.5)
    default_target_pct = Column(Float, default=3.0)

    # Current day tracking
    todays_pnl = Column(Float, default=0.0)
    todays_trades = Column(Float, default=0)
    is_trading_halted = Column(Boolean, default=False)    # Emergency stop flag
    halt_reason = Column(Text, nullable=True)

    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
