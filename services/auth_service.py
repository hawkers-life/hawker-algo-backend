"""
services/auth_service.py — Authentication helpers.
Uses sha256_crypt instead of bcrypt to avoid version conflicts.
"""
import uuid
import redis
from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from passlib.handlers.sha2_crypt import sha256_crypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db
from models.user import User
from config import get_settings
from loguru import logger

settings = get_settings()

# ── Redis for refresh token storage ──────────────────────────────────────────
try:
    _redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    _redis.ping()
except Exception:
    _redis = None
    logger.warning("Redis not available — refresh tokens stored in memory only")

_memory_tokens: dict = {}

bearer_scheme = HTTPBearer(auto_error=False)


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return sha256_crypt.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return sha256_crypt.verify(plain, hashed)
    except Exception:
        return False


# ── JWT tokens ────────────────────────────────────────────────────────────────

def create_access_token(user_id: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": user_id,
        "role": str(role.value) if hasattr(role, "value") else str(role),
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    token = str(uuid.uuid4())
    expire_days = settings.REFRESH_TOKEN_EXPIRE_DAYS
    expire = datetime.now(timezone.utc) + timedelta(days=expire_days)

    if _redis:
        try:
            _redis.setex(
                f"refresh:{token}",
                int(timedelta(days=expire_days).total_seconds()),
                user_id
            )
        except Exception:
            _memory_tokens[token] = (user_id, expire)
    else:
        _memory_tokens[token] = (user_id, expire)

    return token


def verify_refresh_token(token: str) -> Optional[str]:
    if _redis:
        try:
            user_id = _redis.get(f"refresh:{token}")
            return user_id
        except Exception:
            pass

    entry = _memory_tokens.get(token)
    if entry:
        user_id, expire = entry
        if datetime.now(timezone.utc) < expire:
            return user_id
        del _memory_tokens[token]
    return None


def revoke_refresh_token(token: str):
    if _redis:
        try:
            _redis.delete(f"refresh:{token}")
        except Exception:
            pass
    _memory_tokens.pop(token, None)


# ── Current user dependency ───────────────────────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not credentials:
        raise credentials_exception

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        user_id: str = payload.get("sub")
        if not user_id or payload.get("type") != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise credentials_exception

    return user
