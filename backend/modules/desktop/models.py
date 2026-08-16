"""Wallpaper models for the desktop module."""

from pydantic import BaseModel, Field

#: Wallpaper ids are opaque hex strings we mint ourselves. Pinned as a pattern so
#: it is enforced at the route boundary as well as by `_resolve` — a `..` in this
#: position is how a read route becomes an arbitrary-file-read.
WALLPAPER_ID_PATTERN = r"^[0-9a-f]{32}$"


class Wallpaper(BaseModel):
    id: str = Field(pattern=WALLPAPER_ID_PATTERN)
    #: The name the user's file had, kept only for display. Never used to build a
    #: path: that is what `id` is for.
    name: str
    #: `image/png`, `image/jpeg`, … Echoed back on GET.
    content_type: str
    size: int
    #: Seconds since the epoch, from the file's own mtime.
    modified: float
    #: The api-relative path the frontend stores in `BackdropRef.params.url`.
    #: Relative on purpose — the backend origin differs between the dev server, a
    #: packaged desktop build and a LAN-bound node, so an absolute URL baked into
    #: a saved workspace stops resolving the moment any of that changes.
    url: str


class WallpaperList(BaseModel):
    wallpapers: list[Wallpaper] = []
