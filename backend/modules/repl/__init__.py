"""Python REPL module: a backend-resident interpreter per `/ws` connection whose
`dash` SDK drives the UI over the shared socket. See docs/modules/repl.md."""

from backend.modules.repl.manager import ReplManager

__all__ = ["ReplManager"]
