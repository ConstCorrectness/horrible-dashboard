"""Records module: user-defined tables (CRM, intake, anything row-shaped) stored as
real tables in the app database, with an agent write path that proposes rather than
commits. See docs/modules/records.mdx."""

from backend.modules.records.agent_tools import register_records_tools
from backend.modules.records.proposals import push_records_events
from backend.modules.records.routes import router
from backend.modules.records.store import init_records_db

__all__ = [
    "init_records_db",
    "push_records_events",
    "register_records_tools",
    "router",
]
