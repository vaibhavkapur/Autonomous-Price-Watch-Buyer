"""Apply migrations/*.sql to the DATABASE_URL (PostgreSQL) if not yet applied.

Development SQLite databases are created with ``metadata.create_all`` on
startup; this script is the PostgreSQL path used by docker-compose.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

from sqlalchemy import text  # noqa: E402

from persistence import make_engine  # noqa: E402


def main() -> None:
    keys_path = os.environ.get("DEV_KEYS_PATH")
    if keys_path:
        from authorization_profiles.keys import KeyRing

        KeyRing.load_or_create(Path(keys_path))
        print("keys ready", keys_path)

    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        print("DATABASE_URL is not PostgreSQL; nothing to do (SQLite schemas are created at startup)")
        return
    engine = make_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT now())"))
        applied = {r[0] for r in conn.execute(text("SELECT name FROM schema_migrations"))}
    for sql_file in sorted((ROOT / "migrations").glob("*.sql")):
        if sql_file.name in applied:
            continue
        with engine.begin() as conn:
            conn.exec_driver_sql(sql_file.read_text().replace("BEGIN;", "").replace("COMMIT;", ""))
            conn.execute(text("INSERT INTO schema_migrations (name) VALUES (:n)"), {"n": sql_file.name})
        print("applied", sql_file.name)


if __name__ == "__main__":
    main()
