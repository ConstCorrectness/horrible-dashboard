"""Every place a notebook can be published, by id."""

from __future__ import annotations

from backend.modules.notebook.publish.targets.base import PublishTarget
from backend.modules.notebook.publish.targets.cloud import ColabTarget, KaggleTarget
from backend.modules.notebook.publish.targets.gist import GistTarget
from backend.modules.notebook.publish.targets.pages import PagesTarget

_TARGETS: dict[str, PublishTarget] = {
    target.id: target
    for target in (GistTarget(), PagesTarget(), KaggleTarget(), ColabTarget())
}


def all_targets() -> list[PublishTarget]:
    return list(_TARGETS.values())


def get_target(target_id: str) -> PublishTarget | None:
    return _TARGETS.get(target_id)
