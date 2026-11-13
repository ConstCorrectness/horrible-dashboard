"""Import a heavy third-party package on first use rather than at startup.

`backend.app` imports every module's routes, and a route file that does
`import litellm` at the top charges that import to every process start whether
or not a chat is ever sent. Measured on the dev machine: `lancedb` 2.5 s and
`litellm` 2.0 s of a 9.9 s `import backend.app` — and under `uvicorn --reload`
the app is imported twice (once by the reloader, which loads it to validate the
import string, once by the worker), so the port stayed closed for twice that.

`lazy_module` hands back the real module object, registered in `sys.modules`,
whose body runs on the first attribute access. Call sites keep writing
`litellm.acompletion(...)`, tests keep patching attributes on it, and a later
plain `import litellm` anywhere else gets the same object.
"""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType


def lazy_module(name: str) -> ModuleType | None:
    """The module `name`, not yet executed. None when it is not installed.

    Returning None rather than raising matches the `try: import x / except
    ImportError: x = None` idiom this replaces, so `if x is None` guards hold.
    """
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None
    if spec is None or spec.loader is None:
        return None
    spec.loader = importlib.util.LazyLoader(spec.loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
