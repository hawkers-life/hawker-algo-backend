"""
schemas/auth.py — Pydantic models for request/response validation.
These ensure only valid data enters the system.
"""
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from datetime import datetime
from uuid import UUID
import re

class RegisterRequest(BaseModel):
full_name: str
email: EmailStr
password: str
phone: Optional[str] = None

```
@field_validator("full_name")
@classmethod
def validate_name(cls, v):
    if len(v.strip()) < 2:
        raise ValueError("Name must be at least 2 characters")
    if len(v) > 100:
        raise ValueError("Name too long")
    return v.strip()

@field_validator("password")
@classmethod
def validate_password(cls, v):
    """Enforce strong password policy."""
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters")
    if not re.search(r"[A-Z]", v):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", v):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"\d", v):
        raise ValueError("Password must contain at least one number")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
        raise ValueError("Password must contain at least one special character")
    return v

@field_validator("phone")
@classmethod
def validate_phone(cls, v):
    if v and not re.match(r"^\+?[0-9]{10,15}$", v):
        raise ValueError("Invalid phone number")
    return v
```

class LoginRequest(BaseModel):
email: EmailStr
password: str

class TokenResponse(BaseModel):
access_token: str
refresh_token: str
token_type: str = "bearer"
expires_in: int  # seconds

class RefreshTokenRequest(BaseModel):
refresh_token: str

class UserResponse(BaseModel):
id: UUID
email: EmailStr
full_name: str
role: str
subscription_plan: str
is_active: bool
is_verified: bool
sebi_disclaimer_accepted: bool
created_at: datetime

```
class Config:
    from_attributes = True
```

class PasswordResetRequest(BaseModel):
email: EmailStr

class PasswordResetConfirm(BaseModel):
token: str
new_password: str

```
@field_validator("new_password")
@classmethod
def validate_password(cls, v):
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters")
    return v
```

class SEBIAcceptRequest(BaseModel):
accepted: bool
