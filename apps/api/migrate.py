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
    "photos": {
        "object_key": "VARCHAR",
    },
}

# Columns that must stop being NOT NULL. Not additive, so it is listed
# separately and applied only where the dialect supports it: photos.image_png
# is empty for every row written since photos moved to object storage, and
# SQLite cannot drop a constraint in place (a fresh create_all makes it
# nullable anyway, so only an existing Postgres database needs this).
RELAX_NOT_NULL = {"photos": ["image_png"]}


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
        if conn.dialect.name == "postgresql":
            for table, columns in RELAX_NOT_NULL.items():
                if table not in existing_tables:
                    continue
                for column in columns:
                    col = next((c for c in inspector.get_columns(table)
                                if c["name"] == column), None)
                    if col is None or col.get("nullable", True):
                        continue
                    conn.execute(text(f"ALTER TABLE {table} "
                                      f"ALTER COLUMN {column} DROP NOT NULL"))
                    applied.append(f"{table}.{column} nullable")
    if applied:
        log.info("schema migration added: %s", ", ".join(applied))
    return applied
