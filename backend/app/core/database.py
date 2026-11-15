"""Engine and session factory."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings


def make_engine(database: str | None = None):
    return create_engine(get_settings().url(database), pool_pre_ping=True, future=True)


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_session():
    """FastAPI dependency yielding a session."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    db: Session = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
