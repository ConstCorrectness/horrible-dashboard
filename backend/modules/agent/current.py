"""Which roster agent the running turn belongs to, for tools that care.

Backend tool handlers receive only their arguments. A few need to know *who* is
asking — `kb.search` defaults to the calling agent's `knowledge_scope`, so the ML
Expert searches its collections without the model having to restate them. Set for
the duration of a turn by `run_agent_turn`; tool handlers run inside that turn's
task, so the value reaches them through the context.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_current_agent: ContextVar[str] = ContextVar("current_agent_id", default="main")


def current_agent_id() -> str:
    return _current_agent.get()


@contextmanager
def agent_context(agent_id: str) -> Iterator[None]:
    token = _current_agent.set(agent_id)
    try:
        yield
    finally:
        _current_agent.reset(token)
