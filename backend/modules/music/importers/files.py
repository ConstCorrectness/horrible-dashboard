"""Importers for playlist *files*: Apple Music exports, M3U, and CSV.

None of these need an account. Apple Music in particular has no usable API without
a paid Apple Developer membership, but every Apple Music / iTunes install can
`File → Library → Export Playlist…`, as a tab-separated `.txt` (UTF-16) or an XML
plist — both parsed here.

A playlist file only *describes* tracks. The import job matches each description
against the library, then resolves the rest to a source and downloads them. The
exception is an M3U line pointing at a file that exists on this machine: that one is
imported directly (`external_ids["path"]`), no download needed.
"""

from __future__ import annotations

import csv
import io
import plistlib
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from backend.modules import ytdl
from backend.modules.music.providers import (
    DescribedTrack,
    ImportedPlaylist,
    PlaylistSummary,
    ProviderError,
)


def _decode(data: bytes) -> str:
    """Apple's .txt export is UTF-16 with a BOM; everything else is UTF-8-ish."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _seconds(value: Any, *, millis: bool = False) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).strip())
    except ValueError:
        text = str(value).strip()
        if ":" in text:  # "3:45"
            parts = [float(p) for p in text.split(":") if p]
            total = 0.0
            for part in parts:
                total = total * 60 + part
            return total or None
        return None
    return number / 1000 if millis else number


def _stem(name: str) -> str:
    return Path(name).stem or "Imported playlist"


class _FileImporter:
    kind = "file"

    def configured(self) -> bool:
        return True

    async def list_playlists(self) -> list[PlaylistSummary]:
        raise ProviderError(f"{self.id} imports a file; it has no account to list")  # type: ignore[attr-defined]

    async def read_playlist(self, ref: str) -> ImportedPlaylist:
        raise ProviderError(f"{self.id} imports a file; upload one instead")  # type: ignore[attr-defined]


class AppleMusicExport(_FileImporter):
    id = "apple-music"
    label = "Apple Music export"

    def parse(self, filename: str, data: bytes) -> ImportedPlaylist:
        if data.lstrip().startswith(b"<?xml") or b"<plist" in data[:512]:
            return self._parse_xml(filename, data)
        return self._parse_txt(filename, data)

    def _parse_txt(self, filename: str, data: bytes) -> ImportedPlaylist:
        text = _decode(data).replace("\r\n", "\n").replace("\r", "\n")
        rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
        if not rows or "Name" not in rows[0]:
            raise ProviderError("not an Apple Music playlist export (no Name column)")
        header = rows[0]
        col = {name: i for i, name in enumerate(header)}

        def get(row: list[str], name: str) -> str:
            i = col.get(name)
            return row[i].strip() if i is not None and i < len(row) else ""

        tracks = []
        for row in rows[1:]:
            title = get(row, "Name")
            if not title:
                continue
            tracks.append(
                DescribedTrack(
                    title=title,
                    artist=get(row, "Artist"),
                    album=get(row, "Album"),
                    duration=_seconds(get(row, "Time")),
                )
            )
        name = _stem(filename)
        return ImportedPlaylist(
            name=name, origin=f"apple-music:{name.lower()}", tracks=tracks
        )

    def _parse_xml(self, filename: str, data: bytes) -> ImportedPlaylist:
        try:
            plist = plistlib.loads(data)
        except Exception as exc:  # noqa: BLE001 — plistlib raises several types
            raise ProviderError(f"could not read the XML export: {exc}") from exc
        catalog: dict[str, Any] = plist.get("Tracks") or {}
        playlists = [
            p
            for p in plist.get("Playlists") or []
            if not p.get("Master")
            and not p.get("Distinguished Kind")
            and p.get("Playlist Items")
        ]
        note = ""
        if playlists:
            chosen = playlists[0]
            ids = [
                str(item.get("Track ID")) for item in chosen.get("Playlist Items") or []
            ]
            name = str(chosen.get("Name") or _stem(filename))
            if len(playlists) > 1:
                note = f"The file has {len(playlists)} playlists; imported the first, '{name}'."
        else:
            # A whole-library export with no playlists: import every track.
            ids = list(catalog)
            name = _stem(filename)
        tracks = []
        for track_id in ids:
            # plistlib gives the catalog string keys ("1234"), the items int ids.
            info = catalog.get(track_id)
            if not info or not info.get("Name"):
                continue
            external: dict[str, str] = {}
            if info.get("Persistent ID"):
                external["apple"] = str(info["Persistent ID"])
            location = info.get("Location") or ""
            if location.startswith("file://"):
                path = unquote(urlparse(location).path)
                if len(path) > 2 and path[0] == "/" and path[2] == ":":
                    path = path[1:]  # file:///C:/... on Windows
                external["path"] = path
            tracks.append(
                DescribedTrack(
                    title=str(info.get("Name")),
                    artist=str(info.get("Artist") or ""),
                    album=str(info.get("Album") or ""),
                    duration=_seconds(info.get("Total Time"), millis=True),
                    external_ids=external,
                )
            )
        return ImportedPlaylist(
            name=name, origin=f"apple-music:{name.lower()}", tracks=tracks, note=note
        )


class M3UImport(_FileImporter):
    id = "m3u"
    label = "M3U playlist"

    def parse(self, filename: str, data: bytes) -> ImportedPlaylist:
        text = _decode(data)
        tracks: list[DescribedTrack] = []
        pending: tuple[float | None, str] | None = None
        name = _stem(filename)
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.upper().startswith("#PLAYLIST:"):
                name = line.split(":", 1)[1].strip() or name
                continue
            if line.upper().startswith("#EXTINF:"):
                info = line.split(":", 1)[1]
                secs, _, label = info.partition(",")
                duration = _seconds(secs.split()[0] if secs.strip() else "")
                pending = (
                    duration if duration and duration > 0 else None,
                    label.strip(),
                )
                continue
            if line.startswith("#"):
                continue
            duration, label = pending or (None, "")
            pending = None
            external: dict[str, str] = {}
            location = line
            if location.startswith("file://"):
                location = unquote(urlparse(location).path)
            if not location.startswith(("http://", "https://")):
                external["path"] = location
            artist, title = ytdl.split_title(label or Path(unquote(location)).stem)
            if not title:
                continue
            tracks.append(
                DescribedTrack(
                    title=title, artist=artist, duration=duration, external_ids=external
                )
            )
        if not tracks:
            raise ProviderError("no tracks found in the M3U file")
        return ImportedPlaylist(name=name, origin=f"m3u:{name.lower()}", tracks=tracks)


# Column names seen in the wild: Exportify (Spotify), TuneMyMusic, Soundiiz,
# spreadsheet hand-rolls. Lowercased.
_CSV_COLUMNS: dict[str, tuple[str, ...]] = {
    "title": ("track name", "title", "name", "song", "track", "song name"),
    "artist": ("artist name(s)", "artist", "artists", "artist name", "artist(s)"),
    "album": ("album name", "album", "album title"),
    "duration_ms": ("duration (ms)", "duration_ms"),
    "duration": ("duration", "length", "time"),
    "isrc": ("isrc",),
    "spotify": ("track uri", "spotify uri", "uri", "spotify id", "spotify track id"),
}


class CSVImport(_FileImporter):
    id = "csv"
    label = "CSV playlist"

    def parse(self, filename: str, data: bytes) -> ImportedPlaylist:
        text = _decode(data)
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if not reader.fieldnames:
            raise ProviderError("the CSV file has no header row")
        lookup = {name.strip().lower(): name for name in reader.fieldnames}

        def column(key: str) -> str | None:
            for alias in _CSV_COLUMNS[key]:
                if alias in lookup:
                    return lookup[alias]
            return None

        cols = {key: column(key) for key in _CSV_COLUMNS}
        if cols["title"] is None:
            raise ProviderError("the CSV file has no title/track name column")
        tracks = []
        for row in reader:
            title = (row.get(cols["title"]) or "").strip()  # type: ignore[arg-type]
            if not title:
                continue

            def get(key: str) -> str:
                name = cols.get(key)
                return (row.get(name) or "").strip() if name else ""

            # Exportify joins several artists with commas; the first is the one a
            # search needs.
            artist = (
                get("artist").split(",")[0].strip()
                if "," in get("artist")
                else get("artist")
            )
            duration = _seconds(get("duration_ms"), millis=True) or _seconds(
                get("duration")
            )
            external: dict[str, str] = {}
            if isrc := get("isrc"):
                external["isrc"] = isrc
            if spotify := get("spotify"):
                external["spotify"] = spotify.rsplit(":", 1)[-1]
            tracks.append(
                DescribedTrack(
                    title=title,
                    artist=artist,
                    album=get("album"),
                    duration=duration,
                    external_ids=external,
                )
            )
        name = _stem(filename)
        return ImportedPlaylist(name=name, origin=f"csv:{name.lower()}", tracks=tracks)
