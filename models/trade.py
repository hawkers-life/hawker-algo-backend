"""
models/trade.py — Trade execution records.
Every order placed by the system is logged here.
"""
from sqlalchemy import Column, String, Boolean, DateTime, Enum, Float, Integer, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
import enum
from database import Base


class TradeAction(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(str, enum.Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    SQUARED_OFF = "SQUARED_OFF"


class TradeMode(str, enum.Enum):
    PAPER = "paper"
    LIVE = "live"
    FORWARD_TEST = "forward_test"


class Trade(Base):
    __tablename__ = "trades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    strategy_id = Column(UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True)

    # Order details
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), default="NSE")
    action = Column(Enum(TradeAction), nullable=False)
    quantity = Column(Integer, nullable=False)
    order_type = Column(String(20), default="MARKET")  # MARKET, LIMIT, SL, SL-M

    # Price tracking
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    stop_loss_price = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    ltp = Column(Float, nullable=True)  # Last traded price (updated live)

    # P&L
    pnl = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)
    brokerage = Column(Float, default=0.0)
    net_pnl = Column(Float, default=0.0)

    # Status
    status = Column(Enum(TradeStatus), default=TradeStatus.PENDING)
    mode = Column(Enum(TradeMode), default=TradeMode.PAPER)
    broker_order_id = Column(String(100), nullable=True)  # ID from broker API

    # Timestamps
    placed_at = Column(DateTime(timezone=True), server_default=func.now())
    filled_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    # Notes / rejection reason
    notes = Column(Text, nullable=True)

    # Relationships
    user = relationship("User", back_populates="trades")
    strategy = relationship("Strategy", back_populates="trades")
