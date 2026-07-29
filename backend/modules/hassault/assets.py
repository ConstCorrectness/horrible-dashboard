"""The map catalog: maps this app ships, plus an AssaultCube install if there is one.

AssaultCube's **content** is copyright: freely redistributable only as part of an
unmodified AssaultCube package, and never commercially. Its *source* is zlib-like,
which is why the map reader could be ported, but no map, texture, model or sound
may be committed to this repo — which is public, deploys to GitHub Pages, and ships
a game server image to Fly.

That restriction is about *their* content. So the game ships **its own** maps
(`mapsource.py`), built from declarative source and playable with nothing
installed, and **supports** AssaultCube content without ever bundling it: point
`hassault.installPath` at your own copy and its 44 maps appear alongside them.
Same precedent as SearXNG (AGPL, supported but never bundled) and pypdf-over-PyMuPDF.

The two catalogs share one flat namespace, which is safe only because every
bundled map is named `hd_*` and bundled maps are resolved first — neither side
can shadow the other, whatever a user drops into their install directory.

Everything served from the install goes through `resolve_asset`, which refuses to
escape the package root — an install path is user-supplied configuration, and the
map/texture names that index into it come from files we did not write.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from backend.modules.hassault import mapsource
from backend.modules.hassault.cgz import CgzMap, read_cgz
from backend.modules.settings.routes import get_value

# Where AssaultCube usually lands, per platform. Only used when the setting is
# blank; a version-suffixed directory means the glob has to do the walking.
_CANDIDATE_GLOBS = [
    "C:/Program Files (x86)/AssaultCube*",
    "C:/Program Files/AssaultCube*",
    "/usr/share/assaultcube",
    "/usr/local/share/assaultcube",
    "/opt/assaultcube",
    "~/AssaultCube*",
    "~/.assaultcube",
    "/Applications/AssaultCube.app/Contents/gamedata",
]


def _looks_like_install(path: Path) -> bool:
    """A directory is an install if it has the package tree we actually read."""
    return (path / "packages" / "maps").is_dir()


@lru_cache(maxsize=8)
def _autodetect(_cache_key: str) -> str | None:
    for pattern in _CANDIDATE_GLOBS:
        expanded = Path(pattern).expanduser()
        parent, glob = expanded.parent, expanded.name
        if not parent.is_dir():
            continue
        try:
            matches = sorted(parent.glob(glob), reverse=True)  # newest version first
        except OSError:
            continue
        for match in matches:
            if match.is_dir() and _looks_like_install(match):
                return str(match)
    return None


def install_root() -> Path | None:
    """The AssaultCube install to read content from, or None if there isn't one.

    An explicit `hassault.installPath` setting always wins; otherwise the usual
    per-platform locations are probed. Returns None rather than raising so the
    module loads (and can explain itself in the UI) with no install present.
    """
    configured = str(get_value("hassault.installPath", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if _looks_like_install(path) else None
    # Keyed on the environment so a test pointing elsewhere isn't served a
    # cached answer from the developer's real machine.
    detected = _autodetect(os.environ.get("HORRIBLE_DATA_DIR", ""))
    return Path(detected) if detected else None


def packages_root() -> Path | None:
    root = install_root()
    return (root / "packages") if root else None


def map_dirs() -> list[Path]:
    """Directories holding `.cgz` maps, official first."""
    packages = packages_root()
    if packages is None:
        return []
    maps = packages / "maps"
    found = [maps / "official", maps / "servermaps", maps]
    return [d for d in found if d.is_dir()]


def find_map(name: str) -> Path | None:
    """Locate a map by bare name (no extension, no path).

    The name indexes into the install, so it is validated rather than trusted:
    anything with a separator or a dot is refused outright instead of being
    normalized, because a "cleaned" traversal attempt is still an attempt.
    """
    if not name or not all(ch.isalnum() or ch in "-_" for ch in name):
        return None
    for directory in map_dirs():
        candidate = directory / f"{name}.cgz"
        if candidate.is_file():
            return candidate
    return None


def list_maps() -> list[dict[str, str]]:
    """Every playable map: the bundled ones first, then the install's.

    `size` is the size of the file each map actually comes from — a `.cgz` for an
    install map, the JSON source for a bundled one. They are not comparable
    numbers, which is what `source` is for.
    """
    seen: dict[str, dict[str, str]] = {}
    for name in mapsource.bundled_names():
        path = mapsource.MAPS_DIR / f"{name}.json"
        seen[name] = {
            "name": name,
            "source": "bundled",
            "size": str(path.stat().st_size if path.is_file() else 0),
        }
    for directory in map_dirs():
        try:
            entries = sorted(directory.glob("*.cgz"))
        except OSError:
            continue
        for path in entries:
            if path.stem in seen:
                continue
            seen[path.stem] = {
                "name": path.stem,
                "source": path.parent.name,
                "size": str(path.stat().st_size),
            }
    return list(seen.values())


# Parsed maps, keyed by path + mtime. The grid decode is a per-cube Python loop,
# and a map is immutable on disk for as long as anyone cares. Small on purpose:
# a running match and a map being browsed in the pane are the realistic worst
# case, and each 256x256 map is ~590 KB of planes.
_MAP_CACHE_SIZE = 3
_map_cache: dict[str, CgzMap] = {}


def load_map(name: str) -> CgzMap | None:
    """Parse a map by bare name, or `None` if no catalog has such a map.

    Raises `CgzError` for a map that exists but cannot be read — the two failures
    want different messages, so they are not collapsed into one `None`.

    Bundled maps are resolved first. They are the ones this project controls, and
    an install is a directory anyone can drop a file into; letting a stray
    `hd_pit.cgz` there decide what the game loads would be a surprise with no
    upside, since the names are ours by construction.
    """
    bundled = mapsource.load_bundled(name)
    if bundled is not None:
        return bundled
    path = find_map(name)
    if path is None:
        return None
    key = f"{path}:{path.stat().st_mtime_ns}"
    cached = _map_cache.get(key)
    if cached is not None:
        return cached
    parsed = read_cgz(path)
    if len(_map_cache) >= _MAP_CACHE_SIZE:
        _map_cache.clear()
    _map_cache[key] = parsed
    return parsed


def resolve_asset(relative: str) -> Path | None:
    """Resolve a path under `packages/`, refusing anything that escapes it.

    `Path.resolve()` collapses `..` and follows symlinks, so comparing the
    resolved child against the resolved root is what actually decides this —
    checking the raw string for ".." would miss both symlinks and encoded forms.
    """
    packages = packages_root()
    if packages is None or not relative:
        return None
    root = packages.resolve()
    try:
        target = (root / relative).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None
