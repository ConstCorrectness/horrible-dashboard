"""Database-schema extraction for the symdex index.

One document per table across every saved connection (built-in `app` included),
via the same per-provider `driver.introspect` the SQL console's sidebar uses —
so the dba agent can *semantically* find "the table with user emails" without
walking schemas tool-call by tool-call. Sync and network/file-bound — call it on
a thread. A connection that fails to introspect (server down, bad credential)
is skipped with a log line, never fatal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.modules.database.connections import list_connections, resolve_config
from backend.modules.database.drivers import DriverError, get_driver

logger = logging.getLogger(__name__)


@dataclass
class SchemaDoc:
    id: str
    text: str
    metadata: dict[str, Any]


def extract_schemas() -> list[SchemaDoc]:
    docs: list[SchemaDoc] = []
    for conn in list_connections():
        conn_id = str(conn.get("id", ""))
        provider = str(conn.get("provider", ""))
        try:
            schema = get_driver(provider).introspect(resolve_config(conn))
        except (DriverError, Exception) as exc:  # noqa: BLE001 — skip broken connections
            logger.info("symdex schema extract skipped %s: %s", conn_id, exc)
            continue
        for table in schema.tables:
            qualified = f"{table.schema}.{table.name}" if table.schema else table.name
            cols = ", ".join(
                f"{c.name} {c.type}"
                + (" primary key" if c.primary_key else "")
                + ("" if c.nullable else " not null")
                for c in table.columns
            )
            docs.append(
                SchemaDoc(
                    id=f"schema:{conn_id}:{qualified}",
                    text=(
                        f"table {qualified} in database connection "
                        f"{conn.get('name', conn_id)} ({provider})\ncolumns: {cols}"
                    ),
                    metadata={
                        "connection": conn_id,
                        "provider": provider,
                        "table": qualified,
                        "columns": [c.name for c in table.columns],
                    },
                )
            )
    return docs
