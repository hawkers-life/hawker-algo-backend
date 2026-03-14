"""
routers/auth.py — Authentication endpoints.
POST /auth/register   → Create account
POST /auth/login      → Get tokens
POST /auth/refresh    → Get new access token
POST /auth/logout     → Revoke refresh token
POST /auth/sebi-accept → Accept SEBI disclaimer
GET  /auth/me         → Get current user profile
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from database import get_db
from models.user import User, UserRole, SubscriptionPlan
from models.subscription import RiskConfig
from schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse,
    RefreshTokenRequest, UserResponse, SEBIAcceptRequest,
    PasswordResetRequest
)
from services.auth_service import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    verify_refresh_token, revoke_refresh_token,
    get_current_user
)
from config import get_settings
from loguru import logger

router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_settings()


@router.post("/register", response_model=UserResponse, status_code=201)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    """Create a new trader account."""
    # Check email uniqueness
    existing = db.query(User).filter(User.email == data.email.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    # Create user
    user = User(
        email=data.email.lower(),
        full_name=data.full_name,
        phone=data.phone,
        hashed_password=hash_password(data.password),
        role=UserRole.TRADER,
        subscription_plan=SubscriptionPlan.FREE,
        is_active=True,
        is_verified=True,  # In production: send verification email
    )
    db.add(user)
    db.flush()  # Get user.id before committing

    # Create default risk config for new user
    risk_config = RiskConfig(user_id=user.id)
    db.add(risk_config)
    db.commit()
    db.refresh(user)

    logger.info(f"✅ New user registered: {user.email}")
    return user


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Login with email/password. Returns JWT access + refresh tokens."""
    user = db.query(User).filter(User.email == data.email.lower()).first()

    # Generic error — don't reveal if email exists
    auth_error = HTTPException(status_code=401, detail="Invalid email or password")

    if not user:
        raise auth_error

    # Check account lock (after too many failed attempts)
    if user.locked_until and datetime.now(timezone.utc) < user.locked_until.replace(tzinfo=timezone.utc):
        raise HTTPException(status_code=423, detail="Account temporarily locked. Try again in 15 minutes.")

    if not verify_password(data.password, user.hashed_password):
        # Increment failed attempts
        attempts = int(user.failed_login_attempts or "0") + 1
        user.failed_login_attempts = str(attempts)
        if attempts >= 5:
            from datetime import timedelta
            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
            logger.warning(f"🔒 Account locked after {attempts} failed attempts: {user.email}")
        db.commit()
        raise auth_error

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    if user.is_suspended:
        raise HTTPException(status_code=403, detail="Account suspended. Contact support.")

    # Success — reset failed attempts
    user.failed_login_attempts = "0"
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)
    client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    user.last_login_ip = client_ip.split(",")[0].strip()
    db.commit()

    access_token = create_access_token(str(user.id), user.role)
    refresh_token = create_refresh_token(str(user.id))

    logger.info(f"✅ Login: {user.email} from {user.last_login_ip}")
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(data: RefreshTokenRequest, db: Session = Depends(get_db)):
    """Get a new access token using refresh token (no re-login needed)."""
    user_id = verify_refresh_token(data.refresh_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or deactivated")

    # Rotate refresh token (old one is invalidated)
    revoke_refresh_token(data.refresh_token)
    new_access = create_access_token(str(user.id), user.role)
    new_refresh = create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@router.post("/logout")
def logout(data: RefreshTokenRequest, current_user: User = Depends(get_current_user)):
    """Logout — invalidate refresh token."""
    revoke_refresh_token(data.refresh_token)
    logger.info(f"✅ Logout: {current_user.email}")
    return {"message": "Logged out successfully"}


@router.post("/sebi-accept")
def accept_sebi_disclaimer(
    data: SEBIAcceptRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Record user's acceptance of SEBI risk disclaimer."""
    if not data.accepted:
        raise HTTPException(status_code=400, detail="You must accept the disclaimer to continue")

    current_user.sebi_disclaimer_accepted = True
    current_user.sebi_disclaimer_accepted_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "SEBI disclaimer accepted", "accepted_at": current_user.sebi_disclaimer_accepted_at}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Get current logged-in user's profile."""
    return current_user
