"""Driver registry: maps a provider id to its driver module.

Drivers are plain modules exposing ``provider``, ``test``, ``run_query`` and
``introspect`` (see ``base.Driver``). Client libraries are imported lazily inside the
driver, so importing this package never pulls in psycopg/duckdb/pymysql.
"""

from __future__ import annotations

from backend.modules.database.drivers import (
    base,
    chroma_driver,
    duckdb_driver,
    lancedb_driver,
    mongo_driver,
    mysql_driver,
    oracle_driver,
    postgres_driver,
    qdrant_driver,
    sqlite_driver,
    weaviate_driver,
)
from backend.modules.database.drivers.base import Driver, DriverError

_DRIVERS: dict[str, Driver] = {
    sqlite_driver.provider: sqlite_driver,  # type: ignore[dict-item]
    postgres_driver.provider: postgres_driver,  # type: ignore[dict-item]
    duckdb_driver.provider: duckdb_driver,  # type: ignore[dict-item]
    mysql_driver.provider: mysql_driver,  # type: ignore[dict-item]
    oracle_driver.provider: oracle_driver,  # type: ignore[dict-item]
    lancedb_driver.provider: lancedb_driver,  # type: ignore[dict-item]
    chroma_driver.provider: chroma_driver,  # type: ignore[dict-item]
    qdrant_driver.provider: qdrant_driver,  # type: ignore[dict-item]
    weaviate_driver.provider: weaviate_driver,  # type: ignore[dict-item]
    mongo_driver.provider: mongo_driver,  # type: ignore[dict-item]
}

# Provider ids the UI offers, with the connection fields each one expects and the
# query dialect its console editor should use ("sql" or "json" — see base.py).
PROVIDERS: list[dict[str, object]] = [
    {"id": "sqlite", "label": "SQLite file", "fields": ["path"], "dialect": "sql"},
    {
        "id": "postgres",
        "label": "PostgreSQL + pgvector",
        "fields": ["host", "port", "database", "user", "password", "sslmode", "dsn"],
        "dialect": "sql",
    },
    {"id": "duckdb", "label": "DuckDB", "fields": ["path"], "dialect": "sql"},
    {
        "id": "mysql",
        "label": "MySQL / MariaDB",
        "fields": ["host", "port", "database", "user", "password"],
        "dialect": "sql",
    },
    {
        "id": "oracle",
        "label": "Oracle Database 23ai",
        "fields": [
            "host",
            "port",
            "service_name",
            "user",
            "password",
            "dsn",
            "schema",
            "wallet_location",
            "wallet_password",
        ],
        "dialect": "sql",
    },
    {
        "id": "lancedb",
        "label": "LanceDB (vector)",
        "fields": ["path"],
        "dialect": "json",
    },
    {
        "id": "chroma",
        "label": "ChromaDB (vector)",
        "fields": ["path", "host", "port", "ssl", "token"],
        "dialect": "json",
    },
    {
        "id": "qdrant",
        "label": "Qdrant (vector)",
        "fields": ["url", "host", "port", "api_key", "https", "path"],
        "dialect": "json",
    },
    {
        "id": "weaviate",
        "label": "Weaviate (vector)",
        "fields": ["url", "api_key", "host", "port", "grpc_port"],
        "dialect": "json",
    },
    {
        "id": "mongodb",
        "label": "MongoDB / Atlas",
        "fields": [
            "uri",
            "host",
            "port",
            "database",
            "user",
            "password",
            "auth_source",
            "tls",
        ],
        "dialect": "mongo",
    },
]


def get_dialect(provider: str) -> str:
    """The query dialect for a provider id, defaulting to SQL for unknown ones."""
    driver = _DRIVERS.get(provider)
    return str(getattr(driver, "dialect", "sql")) if driver else "sql"


def get_driver(provider: str) -> Driver:
    driver = _DRIVERS.get(provider)
    if driver is None:
        raise DriverError(f"unknown database provider: {provider!r}")
    return driver


__all__ = ["get_driver", "get_dialect", "Driver", "DriverError", "PROVIDERS", "base"]
