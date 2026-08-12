"""How skills reach the model.

Two tiers, and the split is the entire design:

1. **The catalog** — one line per enabled skill (`name: description`), injected as a
   system message on every turn. This is what lets the model know a skill exists.
2. **The body** — delivered only when the model calls `use_skill(name)`.

That is the same progressive disclosure the tool groups already use, and it is here
for the same reason: the body of a good skill is thousands of tokens, and paying for
all of them on every turn would make skills a net loss.

**The catalog is not free, and pretending otherwise is the failure mode.** Every
description rides every round, competing with the tool schemas for the same budget the
interpretability pane exists to make visible. So the catalog is counted, labelled as
its own block kind in that pane (`skills`), and the skills pane shows the running total
before you add another. Ten skills with 40-word descriptions is a permanent ~500-token
tax on a 38-tool budget — which is a fine trade if the skills fire, and pure loss if
they don't.

**`allowed-tools` activates tool groups.** A skill that says "use `files.read` and
`editor.proposeEdit`" is useless if those tools aren't loaded, and asking the model to
notice that and call `load_tools` itself is a round-trip it frequently skips. So
`use_skill` resolves the declared tools to their groups and activates them in the same
step — the skill arrives with its tools already in hand.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.skills import store

logger = logging.getLogger(__name__)

# Marks the catalog system message so the interpretability recorder can label it
# without guessing from position. The recorder classifies system messages by content
# marker (it already does this for the editor buffer); a positional rule would break
# the moment the assembly order changes, and a mislabelled block is worse than none.
CATALOG_MARKER = "## Available skills"

# A ceiling on the whole catalog, not on one description. Past this the message is
# truncated and says so — an unbounded catalog would let a directory of fifty skills
# quietly consume the context the user's actual prompt needs.
MAX_CATALOG_CHARS = 4000


# How long the discovered-skill list is reused before rescanning. This is consulted
# on every model round (twice: once for the tool, once for the catalog), and each scan
# stats two directories and reads every SKILL.md. Two seconds is short enough that
# saving a skill and immediately asking about it works, and long enough that a
# multi-round turn scans once.
_CACHE_TTL_S = 2.0
_cache: tuple[float, list[store.Skill]] | None = None


def active_skills() -> list[store.Skill]:
    """`store.active_skills()` behind a short TTL, for the per-round callers."""
    import time

    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_S:
        return _cache[1]
    skills = store.active_skills()
    _cache = (now, skills)
    return skills


def invalidate() -> None:
    """Drop the cache. Called by the routes after any write, so the pane's "saved"
    and the agent's view of the world can't disagree even for two seconds."""
    global _cache
    _cache = None


def has_active() -> bool:
    """Whether any skill is enabled — the gate on `use_skill` being in core at all."""
    return bool(active_skills())


def catalog_text(skills: list[store.Skill] | None = None) -> str | None:
    """The system-message text listing available skills, or None if there are none.

    Returning None rather than an empty header matters: a user with no skills must pay
    nothing at all for the feature, not a stub message every turn.
    """
    active = active_skills() if skills is None else skills
    if not active:
        return None
    lines = [
        CATALOG_MARKER,
        "Reusable instructions the user has written. When one matches the task, call "
        "`use_skill` with its name to read it BEFORE doing the work — the description "
        "below is a summary, not the instructions.",
    ]
    for skill in active:
        lines.append(f"- `{skill.name}`: {skill.description}")
    text = "\n".join(lines)
    if len(text) > MAX_CATALOG_CHARS:
        text = (
            text[:MAX_CATALOG_CHARS]
            + "\n… (skill list truncated; disable some in the Skills pane)"
        )
    return text


def catalog_message() -> dict[str, Any] | None:
    """The catalog as a provider message, ready to splice into a turn."""
    text = catalog_text()
    return {"role": "system", "content": text} if text else None


def groups_for(skill: store.Skill) -> list[str]:
    """The tool groups a skill's `allowed-tools` implies.

    An entry may be a tool name (`files.read`) or a group name (`files`); both are
    written in the wild and both are unambiguous, since a group name never contains a
    dot. Resolving here rather than making the author write groups keeps the file
    portable — Claude Code's `allowed-tools` lists *tools*.
    """
    groups: list[str] = []
    for entry in skill.allowed_tools:
        group = entry.split(".", 1)[0] if "." in entry else entry
        if group and group not in groups:
            groups.append(group)
    return groups


def use(name: str, active_groups: set[str] | None = None) -> dict[str, Any]:
    """Resolve `use_skill(name)`: the body, plus any tool groups it declared.

    A miss returns the available names rather than a bare error. The model picked from
    a list it was given, so a failure here is nearly always a small transcription
    slip, and handing back the list turns a dead end into a retry.
    """
    active = active_skills()
    match = next((s for s in active if s.name == name), None)
    if match is None:
        return {
            "error": f"no skill named '{name}'",
            "available": [s.name for s in active],
        }
    result: dict[str, Any] = {"skill": match.name, "instructions": match.body}
    if groups := groups_for(match):
        if active_groups is not None:
            active_groups.update(groups)
        # Reported so the model can see the tools arrived — and so the transcript
        # shows *why* a group became active on a turn nobody called load_tools in.
        result["loadedGroups"] = groups
    return result


async def cost_async(tokenizer_repo: str = "") -> dict[str, Any]:
    """What the catalog costs per turn, and each skill's share of it.

    Per skill as well as in total because the actionable question is never "is this
    expensive" but "which of these is expensive" — and the answer is usually one skill
    whose description is a paragraph.
    """
    from backend.modules.interpretability.tokenizer import Counter

    counter = await Counter.create(tokenizer_repo)
    active = store.active_skills()  # uncached: a cost view must not lag a save
    text = catalog_text(active) or ""
    return {
        "skills": [
            {
                "name": s.name,
                "tokens": counter.count(f"- `{s.name}`: {s.description}"),
                "bodyTokens": counter.count(s.body),
            }
            for s in active
        ],
        "catalogTokens": counter.count(text),
        "exact": counter.exact,
        "tokenizer": counter.repo or "",
    }
