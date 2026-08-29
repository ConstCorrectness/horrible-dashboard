"""The one place the app's version comes from.

There were three spellings of it and they had already drifted: ``APP_VERSION`` in
``backend/app.py`` said ``0.1.0`` while ``pyproject.toml`` and ``tauri.conf.json``
both said ``0.2.0``. That was harmless while the version was only ever printed on
``/api/health`` — and stops being harmless the moment anything *resolves* against
it, which is what the native client's install does: it asks GitHub for the release
matching this build, and a version nothing published is a 404 that reads like a
release that failed to upload.

Resolution order follows ``backend/paths.py``'s rule — **the source tree wins**.
A checkout's ``pyproject.toml`` is the truth about what is running there; installed
package metadata can be left over from an older sync and would otherwise shadow it.

The manifest is found **beside the ``backend`` package**, not through
``paths.repo_root()``, and the difference matters in exactly one place that is easy to
miss. ``repo_root()`` requires a ``.git`` — deliberately, because it answers a question
about *where data belongs*, and a packaged install must not write into its own
installation directory. A packaged backend runtime ships ``pyproject.toml`` beside
``backend/`` with no ``.git`` anywhere, so routing this through ``repo_root()`` would
send every packaged install to :data:`FALLBACK_VERSION` — which is only correct until
the literal drifts, and then every prebuilt-client download 404s against a release tag
that was never published. Two different questions, two different anchors.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

#: Last resort only. Kept in step with ``pyproject.toml`` by
#: ``test_fallback_version_matches_pyproject`` (in
#: ``backend/tests/test_hassault_client_install.py``, which is where the version
#: first became load-bearing) — the one thing stopping this file from becoming the
#: fourth drifting copy it exists to prevent.
FALLBACK_VERSION = "0.3.0"


@lru_cache(maxsize=1)
def app_version() -> str:
    """The running app's version, e.g. ``"0.2.0"``.

    Never raises: a version is wanted in places (the health route, the `/ws` hello
    frame) where failing is worse than being approximately right.
    """
    # `parent.parent` is the directory holding the `backend` package: a repo root in a
    # checkout, the runtime root in a packaged install. Both carry `pyproject.toml`.
    root = Path(__file__).resolve().parent.parent
    if root.is_dir():
        try:
            with open(root / "pyproject.toml", "rb") as handle:
                version = tomllib.load(handle).get("project", {}).get("version")
            if isinstance(version, str) and version:
                return version
        except (OSError, tomllib.TOMLDecodeError, AttributeError):
            pass

    try:
        from importlib.metadata import PackageNotFoundError, version as dist_version

        return dist_version("horrible-dashboard")
    except (PackageNotFoundError, ImportError, ValueError):
        return FALLBACK_VERSION
