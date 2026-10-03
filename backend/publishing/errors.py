"""The one exception a publisher raises on purpose."""

from __future__ import annotations


class PublishError(RuntimeError):
    """A failure worth showing the person verbatim — with what to do about it."""
