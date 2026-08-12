"""Custody of SKILL.md files — the Anthropic skill format, read and written here.

A skill is a directory containing `SKILL.md`: YAML frontmatter (`name`,
`description`, optional `allowed-tools`) followed by a markdown body. One format, two
consumers — this app's agent reads it through the progressive-disclosure path, and
Claude Code reads the same directory unchanged if you export it to `.claude/skills/`.
That is the whole point of not inventing a format.

**Two sources, and one of them is read-only.**

- `user` — `$HORRIBLE_DATA_DIR/skills/<name>/SKILL.md`, written here.
- `project` — `<repo>/.claude/skills/<name>/SKILL.md`, *discovered* here.

Project skills are git-tracked files belonging to the repository, so the pane reads
them and offers "copy to my skills" rather than editing them in place. A pane that
silently rewrites a tracked file is a surprise in someone's next `git diff`.

**A name collision shadows rather than duplicates.** `use_skill(name)` takes a name,
so two entries called `review` would make that call ambiguous. The user copy wins and
the project one is reported as `shadowed` — surfaced in the pane, because the failure
this prevents ("I edited the skill and nothing changed") is otherwise invisible.

**A malformed skill is an error, not a silent omission.** A skill that quietly fails
to load is the worst outcome here: the agent simply doesn't know something, and
nothing anywhere says why. So a file that won't parse is listed with its error.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

Scope = Literal["user", "project"]

# The name is the directory name *and* what the model types into `use_skill`, so it is
# constrained to something a model reproduces reliably and a filesystem accepts.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Frontmatter delimiters, at the very start of the file.
_FENCE = "---"

# A description is what rides every single turn (see `agent.py`), so an essay in that
# field is a permanent context tax. Truncated rather than rejected — refusing to load
# a skill over a formatting preference would be worse — but flagged.
MAX_DESCRIPTION_CHARS = 500

# Bodies are loaded on demand, so this is generous. It exists only to stop a runaway
# file (a log accidentally saved as SKILL.md) from being pasted into a turn.
MAX_BODY_CHARS = 60_000


@dataclass
class Skill:
    """One skill as it exists on disk."""

    name: str
    description: str = ""
    body: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    scope: Scope = "user"
    path: str = ""
    error: str = ""
    # True for a project skill that a user skill of the same name is hiding.
    shadowed: bool = False
    # Extra frontmatter keys are preserved on write. Claude Code and future versions
    # of the format may carry fields this app has no opinion about, and dropping them
    # on a round trip would silently degrade someone's skill.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        """Whether this skill can be offered to the model at all."""
        return not self.error and not self.shadowed and bool(self.description)

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "body": self.body,
            "allowedTools": list(self.allowed_tools),
            "scope": self.scope,
            "path": self.path,
            "error": self.error,
            "shadowed": self.shadowed,
        }


# --- parsing ------------------------------------------------------------------


def parse(text: str, *, fallback_name: str = "") -> tuple[dict[str, Any], str, str]:
    """Split SKILL.md into `(frontmatter, body, error)`.

    Returns an error string rather than raising: every caller here is building a list
    for a pane, and one bad file must not take the list with it.
    """
    stripped = text.lstrip("﻿")  # a BOM before `---` hides the fence
    if not stripped.startswith(_FENCE):
        return {}, stripped, "no YAML frontmatter — the file must start with `---`"
    rest = stripped[len(_FENCE) :].lstrip("\r\n")
    end = rest.find(f"\n{_FENCE}")
    if end == -1:
        return {}, stripped, "frontmatter is never closed with a second `---`"
    raw = rest[:end]
    body = rest[end + len(_FENCE) + 1 :].lstrip("\r\n")
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        return {}, body, f"frontmatter is not valid YAML: {exc}"
    if not isinstance(data, dict):
        return {}, body, "frontmatter must be a mapping of keys to values"
    if fallback_name:
        data.setdefault("name", fallback_name)
    return data, body, ""


def format_skill(skill: Skill) -> str:
    """A `Skill` back to SKILL.md text.

    Key order is fixed (`name`, `description`, `allowed-tools`, then anything else)
    rather than left to the dict: a file whose keys shuffle on every save produces a
    diff on every save, which makes a git-tracked skill unreviewable.
    """
    front: dict[str, Any] = {"name": skill.name, "description": skill.description}
    if skill.allowed_tools:
        front["allowed-tools"] = list(skill.allowed_tools)
    front.update({k: v for k, v in skill.extra.items() if k not in front})
    dumped = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    body = skill.body.strip()
    return f"{_FENCE}\n{dumped}\n{_FENCE}\n\n{body}\n"


def _skill_from_file(path: Path, scope: Scope) -> Skill:
    directory = path.parent.name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Skill(name=directory, scope=scope, path=str(path), error=str(exc))

    front, body, error = parse(text, fallback_name=directory)
    # The **directory** is the identity, not the frontmatter's `name`. Everything that
    # addresses a skill — save, delete, export, `use_skill` — needs one answer, and the
    # directory is the one that exists even when the file won't parse. Taking the name
    # from the frontmatter instead means a mismatched file is retrievable under neither
    # identity: not by its directory (the name differs) and not by its name (no such
    # directory), so the pane can't show you the very error it just recorded.
    name = directory
    declared = str(front.get("name") or directory).strip()
    description = str(front.get("description") or "").strip()
    raw_tools = front.get("allowed-tools") or front.get("allowed_tools") or []
    if isinstance(raw_tools, str):
        # `allowed-tools: files, editor` is what people actually write.
        raw_tools = [part.strip() for part in raw_tools.split(",")]
    allowed = [str(t).strip() for t in raw_tools if str(t).strip()]

    if not error and not description:
        # The description is the trigger. Without one the model has no basis to pick
        # the skill, so it would sit in the catalog costing tokens and never fire.
        error = "no `description` — the model has nothing to decide by"
    if not error and declared != directory:
        # `use_skill` is told the directory name, so a file claiming another one will
        # have the model calling a name that resolves to nothing.
        error = (
            f"frontmatter name '{declared}' does not match the directory "
            f"'{directory}' — the directory wins; fix the file"
        )
    if not error and len(body.strip()) == 0:
        error = "the body is empty — there is nothing for the model to read"

    return Skill(
        name=name,
        description=description[:MAX_DESCRIPTION_CHARS],
        body=body[:MAX_BODY_CHARS],
        allowed_tools=allowed,
        scope=scope,
        path=str(path),
        error=error,
        extra={
            k: v
            for k, v in front.items()
            if k not in ("name", "description", "allowed-tools", "allowed_tools")
        },
    )


# --- locations ----------------------------------------------------------------


def user_dir() -> Path:
    """Where skills written here live. Absolute — see `mcp/author.py` for why."""
    root = Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")).resolve()
    return root / "skills"


def project_dir() -> Path:
    """The repo-local `.claude/skills`, so a project's skills show up unprompted.

    Resolved from this file rather than the process CWD: the backend is started from
    several places (`pnpm dev`, `uv run`, the Tauri shell) and a CWD-relative path
    would make a project's skills appear or vanish depending on the launcher.
    """
    return Path(__file__).resolve().parents[3] / ".claude" / "skills"


def validate_name(name: str) -> str | None:
    if not _NAME_RE.match(name or ""):
        return (
            "A skill name must be 1-64 characters of lowercase letters, digits or "
            "hyphens, starting with a letter or digit."
        )
    return None


def _scan(root: Path, scope: Scope) -> list[Skill]:
    if not root.is_dir():
        return []
    out: list[Skill] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        path = child / "SKILL.md"
        if path.is_file():
            out.append(_skill_from_file(path, scope))
    return out


def list_skills() -> list[Skill]:
    """Every discovered skill, user copies first, project duplicates marked shadowed."""
    user = _scan(user_dir(), "user")
    taken = {s.name for s in user}
    project = _scan(project_dir(), "project")
    for skill in project:
        if skill.name in taken:
            skill.shadowed = True
    return user + project


def get(name: str, scope: Scope | None = None) -> Skill | None:
    for skill in list_skills():
        if skill.name == name and (scope is None or skill.scope == scope):
            return skill
    return None


# --- writing ------------------------------------------------------------------


def save(skill: Skill) -> tuple[Skill | None, str | None]:
    """Create or overwrite a **user** skill. Returns `(skill, error)`.

    Deliberately refuses to write a project skill: those are the repository's files,
    and "copy to my skills" is the supported path. See the module docstring.
    """
    if err := validate_name(skill.name):
        return None, err
    if not skill.description.strip():
        return None, "A skill needs a description — it is what the model decides by."
    if skill.scope != "user":
        return None, "Only your own skills are editable here; copy it first."
    directory = user_dir() / skill.name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(format_skill(skill), encoding="utf-8")
    return _skill_from_file(directory / "SKILL.md", "user"), None


def delete(name: str) -> bool:
    """Remove a user skill and its directory. Project skills are never touched."""
    if validate_name(name):
        return False
    directory = user_dir() / name
    if not (directory / "SKILL.md").is_file():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True


def copy_to_user(name: str) -> tuple[Skill | None, str | None]:
    """Copy a project skill into the user's own skills, resources and all.

    The whole directory, not just `SKILL.md`: the format allows a skill to reference
    files beside it, and a copy that dropped them would produce a skill whose body
    points at things that aren't there.
    """
    source = get(name, scope="project")
    if source is None:
        return None, f"no project skill '{name}'"
    target = user_dir() / name
    if (target / "SKILL.md").is_file():
        return None, f"you already have a skill called '{name}'"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(source.path).parent, target, dirs_exist_ok=True)
    return _skill_from_file(target / "SKILL.md", "user"), None


def export_to_project(name: str) -> tuple[str | None, str | None]:
    """Copy a user skill into `.claude/skills/` so Claude Code picks it up.

    A copy, not a symlink. Windows requires elevation (or developer mode) to create
    one, so a symlink implementation would work on the author's machine and fail on
    half the users' — and the failure would arrive at export time with an opaque
    OSError. The cost is that the two diverge after an edit, which the pane says.
    """
    source = get(name, scope="user")
    if source is None:
        return None, f"no skill '{name}'"
    target = project_dir() / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(source.path).parent, target, dirs_exist_ok=True)
    return str(target), None


# --- enablement ---------------------------------------------------------------
#
# Not a setting: `GET /api/settings` hands the whole bag to the browser and this is
# machine-local state about local files. Not frontmatter either — toggling a project
# skill would rewrite a git-tracked file to record a preference.


def _state_path() -> Path:
    return user_dir().parent / "skills-state.json"


def disabled_names() -> set[str]:
    import json

    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    names = data.get("disabled") if isinstance(data, dict) else None
    return {str(n) for n in names} if isinstance(names, list) else set()


def set_enabled(name: str, enabled: bool) -> set[str]:
    import json

    current = disabled_names()
    if enabled:
        current.discard(name)
    else:
        current.add(name)
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"disabled": sorted(current)}, indent=2), encoding="utf-8"
    )
    return current


def is_enabled(name: str) -> bool:
    return name not in disabled_names()


def active_skills() -> list[Skill]:
    """The skills that actually reach the model: usable, and not switched off."""
    return [s for s in list_skills() if s.usable and is_enabled(s.name)]
