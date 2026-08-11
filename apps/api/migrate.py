"""Additive schema migration for the dev SQLite database.

SQLAlchemy's create_all() creates missing tables but never alters existing
ones, so a database created before a column was added silently lacks it and
every insert 500s. This adds any missing columns in place.

Deliberately additive only: no drops, no type changes, no data rewrites. For
production PostgreSQL, use a real migration tool (Alembic) — this exists so a
local database from an earlier build keeps working instead of forcing you to
delete your test data.
"""

import logging

from sqlalchemy import inspect, text

log = logging.getLogger("photobind.migrate")

# table -> column -> DDL type (SQLite-compatible; also valid on PostgreSQL)
ADDITIVE_COLUMNS = {
    "users": {
        "email_verified_at": "TIMESTAMP",
        "terms_accepted_at": "TIMESTAMP",
        "terms_version": "VARCHAR DEFAULT ''",
    },
    "credentials": {
        "decode_rate": "INTEGER DEFAULT 0",
    },
}


def migrate(engine) -> list[str]:
    """Returns the list of changes applied, so startup can log them."""
    applied: list[str] = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in ADDITIVE_COLUMNS.items():
            if table not in existing_tables:
                continue                      # create_all will build it fresh
            have = {c["name"] for c in inspector.get_columns(table)}
            for column, ddl in columns.items():
                if column in have:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                applied.append(f"{table}.{column}")
    if applied:
        log.info("schema migration added: %s", ", ".join(applied))
    return applied
