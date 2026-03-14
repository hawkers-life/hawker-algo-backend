"""
database.py — PostgreSQL connection via SQLAlchemy.
Uses connection pooling for production performance.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from config import get_settings
from loguru import logger

settings = get_settings()

# ── Engine with connection pooling ───────────────────────────────────────────
engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_size=10,          # 10 persistent connections
    max_overflow=20,       # up to 20 extra under load
    pool_pre_ping=True,    # test connection before using (prevents stale conn)
    pool_recycle=3600,     # recycle connections every hour
    echo=settings.DEBUG,   # log SQL only in debug mode
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """
    Dependency injection for FastAPI routes.
    Automatically closes DB session after each request.
    Usage: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """Create all tables on startup if they don't exist."""
    try:
        # Import all models so SQLAlchemy knows about them
        from models import user, strategy, trade, subscription  # noqa: F401
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables initialized")
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        raise
