"""Skills: the SKILL.md format, discovery, and what reaches the model.

Three things carry the weight here.

The **parser**, because SKILL.md is someone else's format and the failure modes are
all silent: a missing description means a skill the model can never trigger, a name
that disagrees with its directory means `use_skill` and the editor address different
things. Every one of those is asserted to produce a visible error rather than an
absent skill.

**Shadowing**, because `use_skill` resolves by name and two skills called `review`
would make that call ambiguous. The user copy winning is the decision; the project one
being *reported* is what makes it debuggable.

**The two-tier cost split**, which is the entire reason skills are worth having: the
description rides every turn, the body does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.modules.skills import agent, store

GOOD = """---
name: tidy
description: Tidy a file the way this project likes it.
allowed-tools:
  - files.read
  - editor.proposeEdit
---

# Tidy

Do the tidy thing.
"""


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A private data dir and a private stand-in for the repo's `.claude/skills`."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "project" / ".claude" / "skills"
    project.mkdir(parents=True)
    monkeypatch.setattr(store, "project_dir", lambda: project)
    agent.invalidate()
    return store.user_dir(), project


def _write(root: Path, name: str, text: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


# --- parsing ------------------------------------------------------------------


def test_a_well_formed_skill_parses(dirs):
    user, _ = dirs
    _write(user, "tidy", GOOD)
    skill = store.get("tidy")
    assert skill is not None
    assert skill.description.startswith("Tidy a file")
    assert skill.allowed_tools == ["files.read", "editor.proposeEdit"]
    assert skill.body.startswith("# Tidy")
    assert skill.error == ""
    assert skill.usable


def test_a_comma_separated_allowed_tools_list_is_accepted(dirs):
    """`allowed-tools: files, editor` is what people actually write."""
    user, _ = dirs
    _write(
        user,
        "t",
        "---\nname: t\ndescription: d\nallowed-tools: files, editor\n---\n\nbody\n",
    )
    skill = store.get("t")
    assert skill is not None and skill.allowed_tools == ["files", "editor"]


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("no frontmatter at all\n", "must start with `---`"),
        ("---\nname: t\ndescription: d\n\nbody\n", "never closed"),
        ("---\nname: [unclosed\n---\n\nbody\n", "not valid YAML"),
        ("---\njust a string\n---\n\nbody\n", "must be a mapping"),
        ("---\nname: t\n---\n\nbody\n", "no `description`"),
        (
            "---\nname: other\ndescription: d\n---\n\nbody\n",
            "does not match the directory",
        ),
        ("---\nname: t\ndescription: d\n---\n", "body is empty"),
    ],
)
def test_a_broken_skill_is_reported_not_skipped(dirs, text: str, fragment: str):
    """A skill that silently fails to load is the worst outcome: the agent simply
    doesn't know something and nothing anywhere says why."""
    user, _ = dirs
    _write(user, "t", text)
    skill = store.get("t")
    assert skill is not None, "the skill vanished instead of reporting a problem"
    assert fragment in skill.error
    assert not skill.usable


def test_a_bom_before_the_fence_does_not_hide_it(dirs):
    """An editor that writes UTF-8 with a BOM would otherwise make every skill it
    touches 'have no frontmatter'."""
    user, _ = dirs
    _write(user, "t", "﻿" + GOOD.replace("name: tidy", "name: t"))
    skill = store.get("t")
    assert skill is not None and skill.error == ""


def test_write_then_read_round_trips_and_keeps_unknown_keys(dirs):
    """A field a newer format (or Claude Code) put there must survive an edit here."""
    user, _ = dirs
    _write(user, "t", "---\nname: t\ndescription: d\nlicense: MIT\n---\n\nbody\n")
    skill = store.get("t")
    assert skill is not None
    skill.body = "new body"
    saved, err = store.save(skill)
    assert err is None and saved is not None
    text = (user / "t" / "SKILL.md").read_text(encoding="utf-8")
    assert "license: MIT" in text
    assert saved.body.strip() == "new body"


def test_frontmatter_key_order_is_stable(dirs):
    """A file whose keys shuffle on every save produces a diff on every save, which
    makes a git-tracked skill unreviewable."""
    user, _ = dirs
    skill = store.Skill(name="t", description="d", body="b", allowed_tools=["files"])
    text = store.format_skill(skill)
    assert (
        text.index("name:") < text.index("description:") < text.index("allowed-tools:")
    )
    assert store.format_skill(skill) == text


# --- discovery ----------------------------------------------------------------


def test_project_skills_are_discovered(dirs):
    _, project = dirs
    _write(project, "tidy", GOOD)
    skill = store.get("tidy")
    assert skill is not None and skill.scope == "project"


def test_a_user_skill_shadows_a_project_skill_of_the_same_name(dirs):
    """`use_skill` takes a name, so the collision has to resolve one way — and be
    visible, or 'I edited it and nothing changed' has no explanation."""
    user, project = dirs
    _write(project, "tidy", GOOD)
    _write(user, "tidy", GOOD.replace("Tidy a file", "MINE"))
    skills = {(s.scope, s.shadowed): s for s in store.list_skills()}
    assert ("user", False) in skills
    assert ("project", True) in skills
    active = agent.active_skills()
    assert [s.description for s in active] == [skills[("user", False)].description]


def test_a_project_skill_cannot_be_written(dirs):
    """They are the repository's files; a pane that rewrites one is a surprise in
    someone's next `git diff`."""
    _, project = dirs
    _write(project, "tidy", GOOD)
    skill = store.get("tidy")
    assert skill is not None
    _, err = store.save(skill)
    assert err is not None


def test_copying_a_project_skill_brings_its_resources(dirs):
    """The format lets a body reference files beside it; a copy that dropped them
    would produce a skill pointing at things that aren't there."""
    user, project = dirs
    _write(project, "tidy", GOOD)
    (project / "tidy" / "reference.md").write_text("details", encoding="utf-8")
    copied, err = store.copy_to_user("tidy")
    assert err is None and copied is not None and copied.scope == "user"
    assert (user / "tidy" / "reference.md").read_text(encoding="utf-8") == "details"


def test_exporting_writes_a_directory_claude_code_can_read(dirs):
    user, project = dirs
    _write(user, "tidy", GOOD)
    path, err = store.export_to_project("tidy")
    assert err is None and path is not None
    exported = project / "tidy" / "SKILL.md"
    assert exported.is_file()
    # Round-trips through the same parser Claude Code's format implies.
    front, body, parse_error = store.parse(exported.read_text(encoding="utf-8"))
    assert parse_error == "" and front["name"] == "tidy" and body


# --- enablement ---------------------------------------------------------------


def test_disabling_removes_a_skill_from_the_catalog_entirely(dirs):
    """The lever that matters: a disabled skill costs nothing per turn."""
    user, _ = dirs
    _write(user, "tidy", GOOD)
    agent.invalidate()
    assert "tidy" in (agent.catalog_text() or "")
    store.set_enabled("tidy", False)
    agent.invalidate()
    assert agent.catalog_text() is None
    assert not agent.has_active()


def test_enablement_is_not_written_into_the_skill_file(dirs):
    """Toggling a project skill must not rewrite a git-tracked file to record a
    local preference."""
    user, _ = dirs
    path = _write(user, "tidy", GOOD)
    before = path.read_text(encoding="utf-8")
    store.set_enabled("tidy", False)
    assert path.read_text(encoding="utf-8") == before


# --- what reaches the model ---------------------------------------------------


def test_no_skills_means_no_message_and_no_tool(dirs):
    """A user with no skills pays nothing at all for the feature — not a stub
    message every turn, and not a tool schema either."""
    from backend.modules.agent.orchestrator import _core_tools, _skills_message

    agent.invalidate()
    assert agent.catalog_text() is None
    assert _skills_message() is None
    assert "use_skill" not in {t["function"]["name"] for t in _core_tools()}


def test_one_skill_puts_use_skill_in_core(dirs):
    from backend.modules.agent.orchestrator import _core_tools, _skills_message

    user, _ = dirs
    _write(user, "tidy", GOOD)
    agent.invalidate()
    assert _skills_message() is not None
    assert "use_skill" in {t["function"]["name"] for t in _core_tools()}


def test_the_catalog_carries_descriptions_and_not_bodies(dirs):
    """The whole economic argument for skills: the trigger is cheap and rides every
    turn, the instructions are expensive and ride only when asked for."""
    user, _ = dirs
    _write(user, "tidy", GOOD.replace("Do the tidy thing.", "SECRET-BODY-MARKER"))
    agent.invalidate()
    text = agent.catalog_text() or ""
    assert "Tidy a file the way this project likes it." in text
    assert "SECRET-BODY-MARKER" not in text


def test_use_skill_returns_the_body_and_activates_its_groups(dirs):
    """A skill that says 'use editor.proposeEdit' is useless if that tool isn't
    loaded, and the model routinely skips the load_tools round."""
    user, _ = dirs
    _write(user, "tidy", GOOD)
    agent.invalidate()
    groups: set[str] = set()
    result = agent.use("tidy", groups)
    assert "Do the tidy thing." in result["instructions"]
    assert result["loadedGroups"] == ["files", "editor"]
    assert groups == {"files", "editor"}


def test_a_skill_cannot_widen_a_scoped_agent(dirs):
    """`allowed-tools` grants tools, and a specialist's `tool_groups` is a boundary —
    so a skill naming `terminal` must not become a way around the roster."""
    import asyncio
    from types import SimpleNamespace

    from backend.modules.agent.orchestrator import _dispatch_call
    from backend.modules.agent.roster import AgentSpec

    user, _ = dirs
    _write(
        user,
        "risky",
        "---\nname: risky\ndescription: d\nallowed-tools: files, terminal\n---\n\nbody\n",
    )
    agent.invalidate()

    spec = AgentSpec(
        id="scoped",
        name="Scoped",
        description="a scoped specialist",
        system_prompt="p",
        tool_groups=["files"],
        preload_groups=["files"],
    )
    groups: set[str] = set()
    call = SimpleNamespace(
        name="use_skill", arguments={"name": "risky"}, arg_error=None
    )
    result = asyncio.run(
        _dispatch_call(SimpleNamespace(), "turn", call, groups, spec)  # type: ignore[arg-type]
    )
    assert "body" in result["instructions"]
    assert groups == {"files"}
    assert result["refusedGroups"] == ["terminal"]


def test_an_unknown_skill_hands_back_the_list(dirs):
    """The model picked from a list it was given, so a miss is nearly always a
    transcription slip — returning the names turns a dead end into a retry."""
    user, _ = dirs
    _write(user, "tidy", GOOD)
    agent.invalidate()
    result = agent.use("tidyy")
    assert result["error"]
    assert result["available"] == ["tidy"]


def test_a_disabled_skill_cannot_be_used_by_name(dirs):
    """Otherwise 'disabled' would only hide it from the catalog while leaving it
    callable by a model that remembers it from earlier in the conversation."""
    user, _ = dirs
    _write(user, "tidy", GOOD)
    store.set_enabled("tidy", False)
    agent.invalidate()
    assert agent.use("tidy").get("error")


def test_a_broken_skill_never_reaches_the_model(dirs):
    user, _ = dirs
    _write(user, "broken", "---\nname: broken\n---\n\nbody\n")
    agent.invalidate()
    assert agent.catalog_text() is None
    assert agent.use("broken").get("error")


def test_the_catalog_is_labelled_as_its_own_block_kind(dirs):
    """`guides` and `skills` have opposite cost profiles — a guide is paid only when
    its group is active, the catalog rides every turn — so folding them together
    would hide the block whose growth most needs watching."""
    from backend.modules.interpretability.recorder import _classify_prompt

    user, _ = dirs
    _write(user, "tidy", GOOD)
    agent.invalidate()
    message = agent.catalog_message()
    assert message is not None
    kinds = _classify_prompt(
        [
            {"role": "system", "content": "system prompt"},
            message,
            {"role": "system", "content": "a tool guide"},
            {"role": "user", "content": "hello"},
        ]
    )
    assert kinds == ["system", "skills", "guides", "user"]
