"""Suites that ship with the repo, resolved beside the ones you write.

The cases used to be Python constructors compiled into the backend, which was
wrong for the same reason a committed `.cgz` would be wrong in `hassault`: it gave
the module **two authoring formats**, one for us and one for everybody else, and
the one we used was neither reviewable as data nor editable in the app that exists
to edit it. A bundled suite is now a plain `.jsonl` in `suites/`, in exactly the
format `store.load_cases` parses and the editor edits.

The resolution rule is `mapsource.py`'s: **bundled content is an addition, never a
prerequisite, and the two catalogs cannot shadow each other.** A bundled suite's id
is prefixed `bundled:`, which a generated user id (12 hex characters) can never
collide with, so "which suite is this" has one answer no matter what you name
yours.

Bundled suites are **read-only**. Writing to one would edit a file in the repo,
which is the opposite of the point — you would lose it on the next pull and it
would show up as a dirty working tree. `fork()` copies one into your data dir and
you own the copy from then on.
"""

from __future__ import annotations

from pathlib import Path

from backend.modules.evals.models import EvalSuite

#: Bundled ids carry this prefix. A user suite id is 12 hex characters from
#: `uuid4().hex`, so the two spaces cannot overlap.
PREFIX = "bundled:"

#: One line per suite file: the name and the blurb the picker shows. Kept here
#: rather than inside the `.jsonl` because a suite file is a list of cases and has
#: nowhere to put a title — and inventing a header record would mean every reader
#: had to know to skip it.
CATALOG: dict[str, tuple[str, str]] = {
    "starter": (
        "Starter: tool calling",
        "The floor, the negatives, discovery through load_tools, and multi-step "
        "cases. Also the worked example of the case format.",
    ),
}


def suites_dir() -> Path:
    return Path(__file__).resolve().parent / "suites"


def is_bundled(suite_id: str) -> bool:
    return suite_id.startswith(PREFIX)


def slug_of(suite_id: str) -> str:
    return suite_id[len(PREFIX) :] if is_bundled(suite_id) else suite_id


def path_for(suite_id: str) -> Path | None:
    """The file behind a bundled id, or `None` for anything not bundled.

    Resolved through the catalog rather than by joining the id onto the directory,
    because an id arrives from a URL: `bundled:../../secrets` must not become a
    path. Only a slug the catalog knows resolves to anything at all — the same
    reasoning that makes `is_managed` gate deletion in the llama.cpp module.
    """
    slug = slug_of(suite_id)
    if slug not in CATALOG:
        return None
    path = suites_dir() / f"{slug}.jsonl"
    return path if path.exists() else None


def list_bundled() -> list[EvalSuite]:
    """Every bundled suite that is actually present on disk."""
    out: list[EvalSuite] = []
    for slug, (name, description) in CATALOG.items():
        path = suites_dir() / f"{slug}.jsonl"
        if not path.exists():
            continue
        out.append(
            EvalSuite(
                id=f"{PREFIX}{slug}",
                name=name,
                description=description,
                path=str(path),
                tags=["bundled"],
                source="bundled",
                read_only=True,
            )
        )
    return out


def get_bundled(suite_id: str) -> EvalSuite | None:
    path = path_for(suite_id)
    if path is None:
        return None
    name, description = CATALOG[slug_of(suite_id)]
    return EvalSuite(
        id=suite_id,
        name=name,
        description=description,
        path=str(path),
        tags=["bundled"],
        source="bundled",
        read_only=True,
    )
