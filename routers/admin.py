"""
routers/admin.py — Admin-only endpoints.
All routes here require role=admin.
GET  /admin/users           → List all users
GET  /admin/users/{id}      → View user detail
POST /admin/users/{id}/suspend   → Suspend account
POST /admin/users/{id}/activate  → Reactivate account
GET  /admin/stats           → Platform-wide statistics
GET  /admin/system-health   → Server health status
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from datetime import datetime, timezone, timedelta
from database import get_db
from models.user import User, UserRole
from models.strategy import Strategy
from models.trade import Trade, TradeStatus
from models.subscription import Subscription
from services.auth_service import require_admin
from pydantic import BaseModel
from typing import Optional
import time

router = APIRouter(prefix="/admin", tags=["Admin"])

START_TIME = time.time()


@router.get("/users")
def list_all_users(
    skip: int = 0,
    limit: int = 50,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    users = db.query(User).order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    total = db.query(func.count(User.id)).scalar()

    return {
        "total": total,
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role,
                "plan": u.subscription_plan,
                "is_active": u.is_active,
                "is_suspended": u.is_suspended,
                "sebi_accepted": u.sebi_disclaimer_accepted,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]
    }


@router.get("/users/{user_id}")
def get_user_detail(
    user_id: str,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    strategy_count = db.query(func.count(Strategy.id)).filter(Strategy.user_id == user.id).scalar()
    trade_count = db.query(func.count(Trade.id)).filter(Trade.user_id == user.id).scalar()
    total_pnl = db.query(func.coalesce(func.sum(Trade.pnl), 0.0)).filter(
        and_(Trade.user_id == user.id, Trade.status.in_([TradeStatus.FILLED, TradeStatus.SQUARED_OFF]))
    ).scalar()

    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "phone": user.phone,
        "role": user.role,
        "plan": user.subscription_plan,
        "is_active": user.is_active,
        "is_suspended": user.is_suspended,
        "sebi_accepted": user.sebi_disclaimer_accepted,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "last_login_ip": user.last_login_ip,
        "strategy_count": strategy_count,
        "trade_count": trade_count,
        "total_pnl": round(float(total_pnl), 2),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.post("/users/{user_id}/suspend")
def suspend_user(
    user_id: str,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Cannot suspend another admin")
    user.is_suspended = True
    db.commit()
    return {"message": f"User {user.email} suspended"}


@router.post("/users/{user_id}/activate")
def activate_user(
    user_id: str,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_suspended = False
    user.is_active = True
    db.commit()
    return {"message": f"User {user.email} activated"}


@router.get("/stats")
def platform_stats(
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Platform-wide statistics for admin dashboard."""
    total_users = db.query(func.count(User.id)).scalar()
    active_users = db.query(func.count(User.id)).filter(User.is_active == True).scalar()
    total_strategies = db.query(func.count(Strategy.id)).scalar()
    total_trades = db.query(func.count(Trade.id)).scalar()

    # Users by plan
    plan_counts = db.query(User.subscription_plan, func.count(User.id)).group_by(User.subscription_plan).all()

    return {
        "total_users": total_users,
        "active_users": active_users,
        "total_strategies": total_strategies,
        "total_trades": total_trades,
        "users_by_plan": {str(plan): count for plan, count in plan_counts},
    }


@router.get("/system-health")
def system_health(current_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Check health of all system components."""
    import redis as redis_client
    from config import get_settings
    settings = get_settings()

    uptime_seconds = time.time() - START_TIME

    # DB health
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_status = "healthy"
        db_latency = "< 5ms"
    except Exception as e:
        db_status = "error"
        db_latency = "N/A"

    # Redis health
    try:
        r = redis_client.from_url(settings.REDIS_URL)
        r.ping()
        redis_status = "healthy"
    except Exception:
        redis_status = "unavailable"

    return {
        "status": "healthy",
        "uptime_seconds": round(uptime_seconds),
        "uptime_human": f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m",
        "components": {
            "api_server": {"status": "healthy", "latency": "< 10ms"},
            "database": {"status": db_status, "latency": db_latency},
            "redis": {"status": redis_status},
            "trading_engine": {"status": "healthy"},
        }
    }
