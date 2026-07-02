"""Push-target registry: where a project's notebook can be sent to run/live."""

from __future__ import annotations

from backend.modules.training.push.base import PushError, PushTarget
from backend.modules.training.push.colab_push import ColabPush
from backend.modules.training.push.kaggle_push import KagglePush

_TARGETS: dict[str, PushTarget] = {t.target: t for t in (KagglePush(), ColabPush())}


def get_target(target_id: str) -> PushTarget:
    target = _TARGETS.get(target_id)
    if target is None:
        raise PushError(f"unknown push target: {target_id}")
    return target


def list_targets() -> list[dict[str, str]]:
    return [{"target": t.target, "label": t.label} for t in _TARGETS.values()]


__all__ = ["PushError", "PushTarget", "get_target", "list_targets"]
