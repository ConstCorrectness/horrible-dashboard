"""Driver registry: maps a provider id to its driver module.

Drivers are plain modules exposing ``provider``, ``test``, ``run_query`` and
``introspect`` (see ``base.Driver``). Client libraries are imported lazily inside the
driver, so importing this package never pulls in psycopg/duckdb/pymysql.
"""

from __future__ import annotations

from backend.modules.database.drivers import (
    base,
    duckdb_driver,
    mysql_driver,
    postgres_driver,
    sqlite_driver,
)
from backend.modules.database.drivers.base import Driver, DriverError

_DRIVERS: dict[str, Driver] = {
    sqlite_driver.provider: sqlite_driver,  # type: ignore[dict-item]
    postgres_driver.provider: postgres_driver,  # type: ignore[dict-item]
    duckdb_driver.provider: duckdb_driver,  # type: ignore[dict-item]
    mysql_driver.provider: mysql_driver,  # type: ignore[dict-item]
}

# Provider ids the UI offers, with the connection fields each one expects.
PROVIDERS: list[dict[str, object]] = [
    {"id": "sqlite", "label": "SQLite file", "fields": ["path"]},
    {
        "id": "postgres",
        "label": "PostgreSQL + pgvector",
        "fields": ["host", "port", "database", "user", "password", "sslmode", "dsn"],
    },
    {"id": "duckdb", "label": "DuckDB", "fields": ["path"]},
    {
        "id": "mysql",
        "label": "MySQL / MariaDB",
        "fields": ["host", "port", "database", "user", "password"],
    },
]


def get_driver(provider: str) -> Driver:
    driver = _DRIVERS.get(provider)
    if driver is None:
        raise DriverError(f"unknown database provider: {provider!r}")
    return driver


__all__ = ["get_driver", "Driver", "DriverError", "PROVIDERS", "base"]
