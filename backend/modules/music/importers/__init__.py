"""Built-in playlist importers. Plugins add more with `host.add_playlist_importer`."""

from backend.modules.music.importers.files import AppleMusicExport, CSVImport, M3UImport
from backend.modules.music.providers import importers


def register_builtin_importers() -> None:
    from backend.modules.music.importers.lastfm_loved import LastfmLoved  # noqa: PLC0415
    from backend.modules.music.importers.spotify import SpotifyImporter  # noqa: PLC0415

    for importer in (
        AppleMusicExport(),
        M3UImport(),
        CSVImport(),
        SpotifyImporter(),
        LastfmLoved(),
    ):
        if importers.get(importer.id) is None:
            importers.register(importer)


__all__ = ["register_builtin_importers"]
