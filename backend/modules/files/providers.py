"""Virtual file roots: non-filesystem sources that browse like directories.

A **provider** claims a URI scheme (`gdrive:`) and answers the read half of the files
API for paths under it. That's what lets Google Drive appear as a root in the file tree
alongside real workspace roots, Colab-style, without the tree or the editor learning
anything about Drive.

**This module contains no provider implementations on purpose.** Providers register
themselves from their own package (the Drive one lives in `connectors/providers/`), so
the dependency runs connectors → files and never the reverse — the files module stays
ignorant of what might mount into it.

**The security boundary is untouched.** `routes._resolve` is the path-traversal check
for real filesystem paths; it is not modified and not reused here. Instead the routes
detect a scheme *before* calling it and dispatch elsewhere entirely. Two consequences
worth stating plainly:

- `_SCHEME` requires **two or more** characters before the colon, because `C:/Users/x`
  is a Windows path, not a URI. A one-character scheme would make every Windows drive
  letter look virtual and route real files into a provider. There's a regression test.
- A virtual root must never enter `routes._roots()`. That list feeds the filesystem
  watcher and the traversal check, neither of which can mean anything for a remote id
  graph. Virtual roots are appended to the `/roots` *response* only.

Providers are read-only in v1 (`read_only = True`); the write routes reject them with a
403 rather than silently doing nothing.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from backend.modules.files.models import DirListing, FileContent, RootInfo

# Two-or-more characters before the colon: 'C:/' must NOT match (see module docstring).
_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]+):/")


@runtime_checkable
class FileProvider(Protocol):
    """The read half of the files API, for one URI scheme."""

    scheme: str
    read_only: bool

    async def roots(self) -> list[RootInfo]:
        """Root entries to show in the tree — empty when unavailable (e.g. the backing
        account isn't connected), which is how a root appears and disappears."""
        ...

    async def list(self, path: str, *, fresh: bool = False) -> DirListing:
        """List a directory. `fresh` bypasses any cache the provider keeps — the tree's
        Refresh button, which otherwise couldn't see a change made elsewhere."""
        ...

    async def read(self, path: str) -> FileContent: ...


_providers: dict[str, FileProvider] = {}


def register(provider: FileProvider) -> None:
    """Register a provider for its scheme. Idempotent — re-registering replaces, so a
    module that re-runs its setup (tests, reload) doesn't accumulate duplicates."""
    _providers[provider.scheme] = provider


def unregister(scheme: str) -> None:
    _providers.pop(scheme, None)


def reset() -> None:
    """Drop every provider. For tests."""
    _providers.clear()


def scheme_of(path: str) -> str | None:
    """The URI scheme of a path, or None if it's an ordinary filesystem path."""
    match = _SCHEME.match(path or "")
    return match.group(1) if match else None


def is_virtual(path: str) -> bool:
    """Whether a path belongs to a *registered* provider.

    Deliberately not "has a scheme": an unknown scheme falls through to the normal
    filesystem resolution, where it is rejected by the traversal boundary. Treating it
    as virtual here would turn a 403 into a confusing 404.
    """
    scheme = scheme_of(path)
    return scheme is not None and scheme in _providers


def provider_for(path: str) -> FileProvider | None:
    scheme = scheme_of(path)
    return _providers.get(scheme) if scheme else None


async def all_roots() -> list[RootInfo]:
    """Roots from every registered provider. A provider that fails is skipped rather
    than taking the whole tree down with it."""
    import logging

    roots: list[RootInfo] = []
    for provider in _providers.values():
        try:
            roots.extend(await provider.roots())
        except Exception:  # noqa: BLE001 — one broken mount must not hide the others
            logging.getLogger(__name__).exception(
                "file provider %s failed to list roots", provider.scheme
            )
    return roots
