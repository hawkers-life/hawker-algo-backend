"""
database.py — PostgreSQL connection via SQLAlchemy.
Uses connection pooling for production performance.
"""
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from config import get_settings
from loguru import logger

settings = get_settings()

engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.DEBUG,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """Create all tables and seed the admin user on startup."""
    try:
        from models import user, strategy, trade, subscription  # noqa: F401
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables initialized")

        # ── Seed admin account ────────────────────────────────────────────────
        _seed_admin()

    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        raise


def _seed_admin():
    """Create the admin account if it doesn't exist yet."""
    if not settings.ADMIN_EMAIL or not settings.FIRST_ADMIN_PASSWORD:
        logger.warning("⚠️  ADMIN_EMAIL or FIRST_ADMIN_PASSWORD not set — skipping admin seed")
        return

    try:
        from models.user import User, UserRole, SubscriptionPlan
        from models.subscription import RiskConfig
        from services.auth_service import hash_password

        db = SessionLocal()
        try:
            existing = db.query(User).filter(
                User.email == settings.ADMIN_EMAIL.lower()
            ).first()

            if existing:
                logger.info(f"✅ Admin account already exists: {settings.ADMIN_EMAIL}")
                return

            admin_user = User(
                email=settings.ADMIN_EMAIL.lower(),
                full_name="Admin",
                hashed_password=hash_password(settings.FIRST_ADMIN_PASSWORD),
                role=UserRole.ADMIN,
                subscription_plan=SubscriptionPlan.FREE,
                is_active=True,
                is_verified=True,
                sebi_disclaimer_accepted=True,
            )
            db.add(admin_user)
            db.flush()

            risk_config = RiskConfig(user_id=admin_user.id)
            db.add(risk_config)
            db.commit()

            logger.info(f"✅ Admin account created: {settings.ADMIN_EMAIL}")

        finally:
            db.close()

    except Exception as e:
        logger.error(f"❌ Admin seed failed: {e}")
