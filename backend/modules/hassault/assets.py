"""The map catalog: the maps this app ships, and nothing else.

The game plays the nine modelled `hd_*` maps (`mapsource.py`) on every platform.
It used to also read AssaultCube's maps out of a local install
(`hassault.installPath`, or a probe of the usual install locations), and that
was removed on 2026-10-03: two catalogs meant two kinds of map in every picker, a
server browser that could list rooms on maps it did not have, and a Cube
heightfield look that the modelled maps had already moved past. The `.cgz`
reader stays, because the Cube grid is still the collision layer every modelled
map is baked into.

`load_map` is the chokepoint every map route, the match server and all three
clients' boot paths go through, which is why a map-designer draft (`draft:<id>`)
resolves here too.
"""

from __future__ import annotations

from backend.modules.hassault import drafts, mapsource
from backend.modules.hassault.cgz import CgzMap


def list_maps() -> list[dict[str, str]]:
    """Every playable map.

    `source` is always `bundled`. It is kept on the wire because the native client
    and older panes still read it.
    """
    maps: list[dict[str, str]] = []
    for name in mapsource.bundled_names():
        path = mapsource.source_path(name)
        maps.append(
            {
                "name": name,
                "source": "bundled",
                "size": str(path.stat().st_size if path is not None else 0),
            }
        )
    return maps


def load_map(name: str) -> CgzMap | None:
    """Parse a map by bare name, or `None` if there is no such map.

    Raises `CgzError` for a map that exists but cannot be read, since that wants a
    different message from a name nobody has.
    """
    # A map being edited resolves first, and it is the reason this function is
    # the only place that needed to change for the designer to work: every map
    # route, and all three clients' boot paths, go through here.
    if name.startswith(drafts.PREFIX):
        return drafts.compiled(name[len(drafts.PREFIX) :])
    return mapsource.load_bundled(name)
