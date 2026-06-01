from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
DATABASE_DIR = PROJECT_ROOT / "database"
DEFAULT_DATABASE_URL = f"sqlite:///{DATABASE_DIR / 'cs2_pickem.sqlite3'}"


def ensure_project_dirs() -> None:
    for path in (RAW_DATA_DIR, PROCESSED_DATA_DIR, DATABASE_DIR):
        path.mkdir(parents=True, exist_ok=True)

