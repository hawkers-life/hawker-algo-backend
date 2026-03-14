"""
models/user.py — User table definition.
Stores all trader account information securely.
"""
from sqlalchemy import Column, String, Boolean, DateTime, Enum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
import enum
from database import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    TRADER = "trader"


class SubscriptionPlan(str, enum.Enum):
    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    ELITE = "elite"


class User(Base):
    __tablename__ = "users"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # Login credentials
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)

    # Profile
    full_name = Column(String(255), nullable=False)
    phone = Column(String(15), nullable=True)

    # Account status
    role = Column(Enum(UserRole), default=UserRole.TRADER, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    is_suspended = Column(Boolean, default=False, nullable=False)

    # SEBI Compliance — user must accept before trading
    sebi_disclaimer_accepted = Column(Boolean, default=False, nullable=False)
    sebi_disclaimer_accepted_at = Column(DateTime(timezone=True), nullable=True)

    # Subscription
    subscription_plan = Column(Enum(SubscriptionPlan), default=SubscriptionPlan.FREE)
    subscription_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Email verification
    email_verification_token = Column(String(255), nullable=True)
    password_reset_token = Column(String(255), nullable=True)
    password_reset_expires = Column(DateTime(timezone=True), nullable=True)

    # Security tracking
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    last_login_ip = Column(String(45), nullable=True)   # IPv6 up to 45 chars
    failed_login_attempts = Column(String(5), default="0")
    locked_until = Column(DateTime(timezone=True), nullable=True)

    # Notification preferences
    telegram_chat_id = Column(String(50), nullable=True)
    whatsapp_number = Column(String(15), nullable=True)
    notify_telegram = Column(Boolean, default=False)
    notify_email = Column(Boolean, default=True)
    notify_whatsapp = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    strategies = relationship("Strategy", back_populates="user", cascade="all, delete-orphan")
    broker_accounts = relationship("BrokerAccount", back_populates="user", cascade="all, delete-orphan")
    trades = relationship("Trade", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"
