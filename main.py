"""
main.py — Hawker Algo Backend Entry Point.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
from loguru import logger
import sys

from config import get_settings
from database import init_db
from middleware.security import SecurityMiddleware

from routers import auth, dashboard, strategies, risk, admin, broker
from routers import backtest as backtest_router
from routers import ai_advisor as ai_router
from routers import execution as execution_router

settings = get_settings()

# ───────────────────────────────── Logging ─────────────────────────────────

logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    level="DEBUG" if settings.DEBUG else "INFO",
)

logger.add(
    "logs/hawker_algo.log",
    rotation="100 MB",
    retention="30 days",
    compression="gz",
    level="INFO",
)

# ───────────────────────────── App Lifespan ────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Hawker Algo Backend starting up...")
    init_db()
    logger.info(f"✅ Environment: {settings.ENVIRONMENT}")
    logger.info("✅ Database connected")
    yield
    logger.info("👋 Hawker Algo Backend shutting down...")

# ───────────────────────────── FastAPI App ─────────────────────────────────

app = FastAPI(
    title="Hawker Algo API",
    description="Professional Algorithmic Trading Platform for Indian Markets",
    version="1.0.0",
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url="/api/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

# ───────────────────────────── Middleware Order ────────────────────────────
# NOTE:
# Last added middleware runs FIRST

# GZip (compress responses)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Security middleware
app.add_middleware(SecurityMiddleware)

# CORS middleware (must run FIRST to handle OPTIONS preflight)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://hawkers-life.netlify.app",
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ───────────────────────────── Routers ─────────────────────────────────────

API_PREFIX = "/api/v1"

app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(dashboard.router, prefix=API_PREFIX)
app.include_router(strategies.router, prefix=API_PREFIX)
app.include_router(risk.router, prefix=API_PREFIX)
app.include_router(admin.router, prefix=API_PREFIX)
app.include_router(broker.router, prefix=API_PREFIX)
app.include_router(backtest_router.router, prefix=API_PREFIX)
app.include_router(ai_router.router, prefix=API_PREFIX)
app.include_router(execution_router.router, prefix=API_PREFIX)

# ───────────────────────────── Health Routes ───────────────────────────────

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "Hawker Algo API",
        "version": settings.APP_VERSION,
    }

@app.get("/")
def root():
    return {
        "message": "Hawker Algo API. Visit /api/docs for documentation (dev only)."
    }
