"""Example backend plugin: a music *source* backed by a folder (a NAS share, an old
iTunes folder, a USB drive).

It shows the whole music-provider contract in ~80 lines: `search()` matches files by
name, `fetch()` copies the chosen file into the library. Once registered, nothing
else is needed — `music.play "Song by Artist"`, the music pane's search, and every
playlist import's resolver all consider this folder alongside YouTube, and the
library still owns its copy.

Point it at a folder with `MUSIC_FOLDER_SOURCE_DIR`. Install it like any backend
plugin (``backend/plugins/``, ``HORRIBLE_PLUGINS_DIR``, or a ``horrible.plugins``
entry point). See docs/architecture/backend-plugin-sdk.mdx.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from backend.modules.music.providers import FetchedFile, ProviderError, TrackCandidate
from backend.sdk import BackendPlugin, PluginHost, PluginManifest

_AUDIO = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".aac"}


def _root() -> Path | None:
    raw = os.environ.get("MUSIC_FOLDER_SOURCE_DIR", "")
    path = Path(raw).expanduser() if raw else None
    return path if path and path.is_dir() else None


def _split(stem: str) -> tuple[str, str]:
    artist, sep, title = stem.partition(" - ")
    return (artist.strip(), title.strip()) if sep else ("", stem.strip())


class FolderSource:
    id = "folder"
    label = "Music folder"

    def configured(self) -> bool:
        return _root() is not None

    async def search(self, query: str, *, limit: int = 10) -> list[TrackCandidate]:
        root = _root()
        if root is None:
            return []
        words = [
            w
            for w in query.lower().replace(" - ", " ").split()
            if w not in {"by", "the"}
        ]

        def scan() -> list[TrackCandidate]:
            hits = []
            for path in root.rglob("*"):
                if path.suffix.lower() not in _AUDIO:
                    continue
                name = path.stem.lower()
                if all(w in name or w in path.parent.name.lower() for w in words):
                    artist, title = _split(path.stem)
                    # The ref is the path relative to the root, so a candidate can
                    # never address a file outside it.
                    hits.append(
                        TrackCandidate(
                            source=self.id,
                            ref=str(path.relative_to(root)),
                            title=title,
                            artist=artist or path.parent.name,
                            channel=self.label,
                        )
                    )
                    if len(hits) >= limit:
                        break
            return hits

        return await asyncio.to_thread(scan)

    async def fetch(
        self, candidate: TrackCandidate, target: Path, file_id: str
    ) -> FetchedFile:
        root = _root()
        if root is None:
            raise ProviderError("MUSIC_FOLDER_SOURCE_DIR is not set")
        src = (root / candidate.ref).resolve()
        if root.resolve() not in src.parents or not src.is_file():
            raise ProviderError("that file is not in the music folder")
        dest = target / f"{file_id}{src.suffix.lower()}"
        await asyncio.to_thread(shutil.copy2, src, dest)
        return FetchedFile(path=dest, title=candidate.title, artist=candidate.artist)


class MusicFolderPlugin(BackendPlugin):
    manifest = PluginManifest(
        id="music_folder_source",
        name="Music folder source",
        version="1.0.0",
        description="Search a folder of music and copy picks into the library.",
    )

    def setup(self, host: PluginHost) -> None:
        host.add_music_source(FolderSource())


PLUGIN = MusicFolderPlugin()
