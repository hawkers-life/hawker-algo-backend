"""
middleware/security.py — All security hardening in one place.
Covers: Rate limiting, security headers, SQL injection hints,
brute-force protection, suspicious request detection.
"""
import time
import re
from typing import Callable
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger
import redis as redis_client
from config import get_settings

settings = get_settings()

# ── Redis for rate limit tracking ────────────────────────────────────────────
try:
    r = redis_client.from_url(settings.REDIS_URL, decode_responses=True)
except Exception:
    r = None
    logger.warning("Redis not available — rate limiting disabled")


# ── Suspicious patterns to block ─────────────────────────────────────────────
ATTACK_PATTERNS = [
    r"(\bUNION\b.*\bSELECT\b)",
    r"(\bDROP\b.*\bTABLE\b)",
    r"(<script[\s\S]*?>[\s\S]*?</script>)",
    r"(javascript:)",
    r"(\.\./\.\./)",
    r"(/etc/passwd)",
    r"(eval\s*\()",
]
COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in ATTACK_PATTERNS]


def is_suspicious(text: str) -> bool:
    return any(pattern.search(text) for pattern in COMPILED_PATTERNS)


class SecurityMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next: Callable) -> Response:

        # ── CRITICAL: Let OPTIONS (CORS preflight) pass through untouched ──
        if request.method == "OPTIONS":
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        path = request.url.path

        # ── 1. Block suspicious request content ──────────────────────────────
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.body()
                body_text = body.decode("utf-8", errors="ignore")
                if is_suspicious(body_text):
                    logger.warning(f"🚨 Suspicious payload blocked from {client_ip} → {path}")
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Invalid request content"}
                    )
                async def receive():
                    return {"type": "http.request", "body": body}
                request._receive = receive
            except Exception:
                pass

        # ── 2. Rate limiting ──────────────────────────────────────────────────
        if r:
            limit = settings.LOGIN_RATE_LIMIT_PER_MINUTE if "/auth/login" in path \
                else settings.RATE_LIMIT_PER_MINUTE
            key = f"rate:{client_ip}:{path[:30]}"
            current = r.get(key)

            if current and int(current) >= limit:
                logger.warning(f"🚨 Rate limit hit: {client_ip} → {path}")
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please slow down."},
                    headers={"Retry-After": "60"}
                )
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, 60)
            pipe.execute()

        # ── 3. Process request ────────────────────────────────────────────────
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time

        # ── 4. Add security headers ───────────────────────────────────────────
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["X-Process-Time"] = str(round(process_time * 1000, 2)) + "ms"
        response.headers["Server"] = "Hawker-Algo"

        return response

    def _get_client_ip(self, request: Request) -> str:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
