"""The embedded terminal: PTY sessions over the `terminal` WS channel.

`shells` is the catalog the pane's picker reads — the reason this module has an HTTP
surface at all. See docs/modules/terminal.mdx.
"""

from backend.modules.terminal.manager import TerminalManager
from backend.modules.terminal.routes import router

__all__ = ["TerminalManager", "router"]
