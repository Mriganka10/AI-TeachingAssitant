from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


if settings.database_url.startswith("sqlite"):
    engine_options = {"connect_args": {"check_same_thread": False}}
else:
    engine_options = {
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_recycle": settings.database_pool_recycle_seconds,
        "connect_args": {"connect_timeout": settings.database_connect_timeout_seconds},
    }

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    **engine_options,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_schema() -> None:
    from app.core import models  # noqa: F401

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
