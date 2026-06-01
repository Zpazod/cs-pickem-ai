from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.config import DEFAULT_DATABASE_URL, ensure_project_dirs


class Base(DeclarativeBase):
    pass


ensure_project_dirs()
engine = create_engine(DEFAULT_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from backend.database import models  # noqa: F401

    for table in Base.metadata.sorted_tables:
        try:
            table.create(bind=engine, checkfirst=True)
        except OperationalError as exc:
            if "already exists" not in str(exc).lower():
                raise
    _apply_sqlite_lightweight_migrations()


def _apply_sqlite_lightweight_migrations() -> None:
    """Add nullable columns introduced during MVP iterations to existing SQLite DBs."""
    if not engine.url.get_backend_name().startswith("sqlite"):
        return

    additions = {
        "players": {
            "current_status": "VARCHAR(32) DEFAULT 'active'",
        },
        "player_map_stats": {
            "side": "VARCHAR(8) DEFAULT 'both'",
            "swing": "FLOAT",
            "dpr": "FLOAT",
            "kpr": "FLOAT",
            "multikill_rounds": "INTEGER",
            "firepower": "FLOAT",
            "entrying": "FLOAT",
            "trading": "FLOAT",
            "clutching": "FLOAT",
            "utility": "FLOAT",
            "sniping": "FLOAT",
            "opening": "FLOAT",
            "opening_kills": "INTEGER",
            "opening_deaths": "INTEGER",
            "clutches_won": "INTEGER",
            "clutches_attempted": "INTEGER",
            "utility_damage": "FLOAT",
            "flash_assists": "FLOAT",
        },
    }

    inspector = inspect(engine)
    with engine.begin() as connection:
        for table_name, columns in additions.items():
            if not inspector.has_table(table_name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, ddl_type in columns.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_type}"))
