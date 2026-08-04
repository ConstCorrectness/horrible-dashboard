"""Proactive notifications: what reaches the user, and the standing rules that
decide. See docs/modules/notifications.mdx."""

from backend.modules.notifications.service import register as register_notifications

__all__ = ["register_notifications"]
