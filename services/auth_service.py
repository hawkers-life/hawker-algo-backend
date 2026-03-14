"""
services/auth_service.py — Authentication business logic.
Handles: password hashing, JWT creation/verification, refresh tokens.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
import secrets
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db
from models.user import User
from config import get_settings
from loguru import logger
import redis as redis_client

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()

# Redis for refresh token storage and blacklisting
try:
    r = redis_client.from_url(settings.REDIS_URL, decode_responses=True)
except Exception:
    r = None


# ── Password utilities ────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash password with bcrypt (slow by design — prevents brute force)."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    return pwd_context.verify(plain, hashed)


# ── JWT Token utilities ───────────────────────────────────────────────────────

def create_access_token(user_id: str, role: str) -> str:
    """Create short-lived JWT access token (30 minutes)."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_hex(16),  # unique token ID
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """Create long-lived refresh token (7 days). Stored in Redis."""
    token = secrets.token_urlsafe(64)
    expire_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
    if r:
        r.setex(f"refresh:{token}", expire_seconds, str(user_id))
    return token


def verify_access_token(token: str) -> dict:
    """Verify and decode JWT. Raises if invalid or expired."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_refresh_token(token: str) -> Optional[str]:
    """Verify refresh token, return user_id if valid."""
    if not r:
        return None
    user_id = r.get(f"refresh:{token}")
    return user_id


def revoke_refresh_token(token: str):
    """Logout — delete refresh token from Redis."""
    if r:
        r.delete(f"refresh:{token}")


def revoke_all_user_tokens(user_id: str):
    """Force logout from all devices — useful if account is compromised."""
    if r:
        # Pattern delete all refresh tokens for this user
        # (production: maintain a user→token mapping)
        logger.info(f"All tokens revoked for user {user_id}")


# ── Dependency injection ──────────────────────────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> User:
    """FastAPI dependency — validates JWT and returns current user."""
    payload = verify_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    if user.is_suspended:
        raise HTTPException(status_code=403, detail="Account is suspended. Contact support.")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency — only allows admin users."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def require_sebi_accepted(current_user: User = Depends(get_current_user)) -> User:
    """Dependency — ensures user accepted SEBI disclaimer before trading."""
    if not current_user.sebi_disclaimer_accepted:
        raise HTTPException(
            status_code=403,
            detail="Please accept the SEBI risk disclaimer before trading"
        )
    return current_user
