from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.config import DEFAULT_DATABASE_URL, ensure_project_dirs


class Base(DeclarativeBase):
    pass


ensure_project_dirs()
engine = create_engine(DEFAULT_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from backend.database import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

