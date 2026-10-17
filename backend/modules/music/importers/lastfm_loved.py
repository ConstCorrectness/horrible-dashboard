"""Re-export: the loved-tracks importer lives with the rest of Last.fm."""

from backend.modules.music.listeners.lastfm import LastfmLovedImporter as LastfmLoved

__all__ = ["LastfmLoved"]
