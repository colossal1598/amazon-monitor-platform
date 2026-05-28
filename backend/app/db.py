"""Postgres access via a psycopg3 connection pool.

FastAPI route handlers are written as sync functions (run in a threadpool),
so a synchronous pool keeps the code simple and robust.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

LOGGER = logging.getLogger("backend.db")

_pool: ConnectionPool | None = None

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def init_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=True,
        )
    return _pool


def get_pool() -> ConnectionPool:
    if _pool is None:
        return init_pool()
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as conn:
        yield conn


def query(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            return cur.fetchall()


def query_one(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | dict | None = None) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def wait_for_db(timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with psycopg.connect(get_settings().database_url, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            return
        except Exception as exc:  # noqa: BLE001 - retry any connection error
            last_err = exc
            LOGGER.warning("Waiting for database...", extra={"context": {"error": str(exc)}})
            time.sleep(2)
    raise RuntimeError(f"Database not reachable within {timeout_seconds}s: {last_err}")


def run_migrations() -> None:
    """Apply all .sql files in migrations/ in lexical order (idempotent)."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with connection() as conn:
        for path in files:
            sql = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
            LOGGER.info("Applied migration", extra={"context": {"file": path.name}})
