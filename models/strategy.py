"""
models/strategy.py — Strategy and BrokerAccount table definitions.
"""
from sqlalchemy import Column, String, Boolean, DateTime, Enum, Text, Float, Integer, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
import enum
from database import Base


class StrategyType(str, enum.Enum):
    INTRADAY = "intraday"
    SWING = "swing"
    OPTIONS = "options"
    FUTURES = "futures"


class StrategyStatus(str, enum.Enum):
    DRAFT = "draft"
    BACKTESTING = "backtesting"
    PAPER_TRADING = "paper_trading"
    FORWARD_TESTING = "forward_testing"
    LIVE = "live"
    STOPPED = "stopped"
    PAUSED = "paused"


class BrokerName(str, enum.Enum):
    ZERODHA = "zerodha"
    ANGEL_ONE = "angel_one"
    DHAN = "dhan"
    GROWW = "groww"


class Strategy(Base):
    __tablename__ = "strategies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Identity
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    strategy_type = Column(Enum(StrategyType), nullable=False)
    is_prebuilt = Column(Boolean, default=False)   # True = system template

    # Instrument configuration
    instrument = Column(String(50), nullable=False)  # e.g. NIFTY, BANKNIFTY, RELIANCE
    exchange = Column(String(10), default="NSE")
    timeframe = Column(String(10), default="15m")    # 1m, 5m, 15m, 1h, 1d

    # Strategy logic stored as JSON (from no-code builder)
    entry_conditions = Column(JSON, nullable=True)
    exit_conditions = Column(JSON, nullable=True)
    indicators = Column(JSON, nullable=True)

    # Capital & risk per strategy
    allocated_capital = Column(Float, default=100000.0)
    max_quantity = Column(Integer, default=1)
    stop_loss_pct = Column(Float, default=1.5)
    target_pct = Column(Float, default=3.0)
    trailing_sl = Column(Boolean, default=False)
    trailing_sl_pct = Column(Float, nullable=True)

    # Status
    status = Column(Enum(StrategyStatus), default=StrategyStatus.DRAFT)
    broker = Column(Enum(BrokerName), nullable=True)

    # Performance metrics (updated after each trade)
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    total_pnl = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)

    # Backtest results stored as JSON
    backtest_results = Column(JSON, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_trade_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", back_populates="strategies")
    trades = relationship("Trade", back_populates="strategy", cascade="all, delete-orphan")


class BrokerAccount(Base):
    """
    Stores user's broker API credentials.
    API keys are ENCRYPTED at rest using Fernet symmetric encryption.
    Never stored in plain text.
    """
    __tablename__ = "broker_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    broker = Column(Enum(BrokerName), nullable=False)
    display_name = Column(String(100), nullable=True)

    # Encrypted credentials — NEVER store plain text API keys
    encrypted_api_key = Column(Text, nullable=True)
    encrypted_api_secret = Column(Text, nullable=True)
    encrypted_access_token = Column(Text, nullable=True)
    client_id = Column(String(100), nullable=True)  # non-sensitive, stored plain

    is_connected = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    last_connected_at = Column(DateTime(timezone=True), nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="broker_accounts")
