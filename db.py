import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "mlb.db"
SCHEMA_PATH = Path(__file__).parent / "schema" / "schema.sql"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init():
    conn = connect()
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    conn.close()
    print(f"DB initialized at {DB_PATH}")


if __name__ == "__main__":
    init()
