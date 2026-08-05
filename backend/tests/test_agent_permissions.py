"""Tests for the agent permission engine (A4 core)."""

from backend.modules.agent import permissions as P
from backend.modules.agent.permissions import Decision, Mode, Rule, RuleSet, evaluate


def _ev(tool, specifier=None, side_effect=True, mode=Mode.DEFAULT, rules=None):
    return evaluate(tool, specifier, side_effect, mode, rules or RuleSet())


# --- rule parsing & matching ------------------------------------------------


def test_parse_bare_tool() -> None:
    r = Rule.parse("terminal.exec")
    assert r == Rule("terminal.exec", None)


def test_parse_scoped_rule() -> None:
    r = Rule.parse("terminal.exec(npm run *)")
    assert r.tool == "terminal.exec"
    assert r.specifier == "npm run *"


def test_bare_rule_matches_any_use() -> None:
    r = Rule.parse("files.delete")
    assert r.matches("files.delete", "/a/b.txt")
    assert r.matches("files.delete", None)


def test_scoped_rule_glob() -> None:
    r = Rule.parse("terminal.exec(npm run *)")
    assert r.matches("terminal.exec", "npm run build")
    assert not r.matches("terminal.exec", "pytest")


def test_scoped_rule_requires_specifier() -> None:
    r = Rule.parse("files.write(/src/*)")
    assert not r.matches("files.write", None)


def test_tool_name_glob() -> None:
    r = Rule.parse("files.*")
    assert r.matches("files.delete", "/x")
    assert not r.matches("editor.save", "/x")


# --- precedence: deny → ask → allow -----------------------------------------


def test_deny_beats_allow() -> None:
    rules = RuleSet.from_strings(allow=["files.delete"], deny=["files.delete"])
    assert _ev("files.delete", "/x", rules=rules) is Decision.DENY


def test_ask_beats_more_specific_allow() -> None:
    rules = RuleSet.from_strings(
        allow=["files.write(/src/foo.ts)"], ask=["files.write(/src/*)"]
    )
    assert _ev("files.write", "/src/foo.ts", rules=rules) is Decision.ASK


def test_allow_rule_allows() -> None:
    rules = RuleSet.from_strings(allow=["terminal.exec(npm run *)"])
    assert _ev("terminal.exec", "npm run build", rules=rules) is Decision.ALLOW


def test_deny_beats_autonomous() -> None:
    rules = RuleSet.from_strings(deny=["files.delete"])
    assert _ev("files.delete", "/x", mode=Mode.AUTONOMOUS, rules=rules) is Decision.DENY


# --- modes ------------------------------------------------------------------


def test_default_prompts_uncovered_side_effect() -> None:
    assert _ev("files.delete", "/x", mode=Mode.DEFAULT) is Decision.ASK


def test_plan_denies_side_effects_but_allows_reads() -> None:
    assert _ev("files.write", "/x", side_effect=True, mode=Mode.PLAN) is Decision.DENY
    assert _ev("files.read", "/x", side_effect=False, mode=Mode.PLAN) is Decision.ALLOW


def test_autonomous_allows_uncovered_side_effect() -> None:
    assert _ev("editor.save", "/x", mode=Mode.AUTONOMOUS) is Decision.ALLOW


def test_accept_edits_allows_safe_edit_but_prompts_delete() -> None:
    assert _ev("editor.save", "note:1", mode=Mode.ACCEPT_EDITS) is Decision.ALLOW
    assert _ev("files.create", "/src/new.ts", mode=Mode.ACCEPT_EDITS) is Decision.ALLOW
    assert _ev("files.delete", "/src/old.ts", mode=Mode.ACCEPT_EDITS) is Decision.ASK
    assert _ev("terminal.exec", "rm foo", mode=Mode.ACCEPT_EDITS) is Decision.ASK


def test_read_only_never_gated_in_any_mode() -> None:
    for mode in Mode:
        assert _ev("files.list", "/x", side_effect=False, mode=mode) is Decision.ALLOW


# --- circuit breakers -------------------------------------------------------


def test_rm_rf_root_always_prompts_even_autonomous() -> None:
    assert _ev("terminal.exec", "rm -rf /", mode=Mode.AUTONOMOUS) is Decision.ASK
    assert _ev("terminal.exec", "rm -rf ~/data", mode=Mode.AUTONOMOUS) is Decision.ASK


def test_circuit_breaker_does_not_trip_on_safe_command() -> None:
    assert _ev("terminal.exec", "ls -la", mode=Mode.AUTONOMOUS) is Decision.ALLOW


def test_explicit_deny_still_beats_circuit_breaker() -> None:
    rules = RuleSet.from_strings(deny=["terminal.exec"])
    assert (
        _ev("terminal.exec", "rm -rf /", mode=Mode.AUTONOMOUS, rules=rules)
        is Decision.DENY
    )


def test_register_circuit_breaker() -> None:
    before = len(P._circuit_breakers)
    P.register_circuit_breaker(lambda tool, spec: tool == "danger.tool")
    try:
        assert _ev("danger.tool", "x", mode=Mode.AUTONOMOUS) is Decision.ASK
    finally:
        P._circuit_breakers.pop()
    assert len(P._circuit_breakers) == before


# --- specifier rendering ----------------------------------------------------


def test_render_specifier_fills_placeholders() -> None:
    assert (
        P.render_specifier("{command}", {"command": "npm run build"}) == "npm run build"
    )


def test_render_specifier_none_template() -> None:
    assert P.render_specifier(None, {}) is None


def test_render_specifier_missing_arg_is_empty() -> None:
    assert P.render_specifier("{path}", {}) == ""


def test_rendered_specifier_matches_rule() -> None:
    rules = RuleSet.from_strings(allow=["terminal.exec(npm run *)"])
    spec = P.render_specifier("{command}", {"command": "npm run test"})
    assert _ev("terminal.exec", spec, rules=rules) is Decision.ALLOW


# --- A4b: shell-aware specifier matching ------------------------------------


def _exec(command, mode=Mode.DEFAULT, rules=None):
    return evaluate("terminal.exec", command, True, mode, rules or RuleSet())


def test_word_boundary_glob() -> None:
    rules = RuleSet.from_strings(allow=["terminal.exec(npm run *)"])
    assert _exec("npm run build", rules=rules) is Decision.ALLOW
    # `npm run *` must not match `npm runner` (the space is the boundary).
    assert _exec("npm runner", rules=rules) is Decision.ASK


def test_read_only_command_allowed_in_every_mode() -> None:
    for mode in Mode:
        assert _exec("ls -la", mode=mode) is Decision.ALLOW
        assert _exec("cat README.md", mode=mode) is Decision.ALLOW


def test_read_only_with_redirect_is_not_read_only() -> None:
    # `cat x > y` writes — must not be auto-allowed.
    assert _exec("cat a.txt > b.txt", mode=Mode.PLAN) is Decision.DENY


def test_compound_allow_requires_every_subcommand_covered() -> None:
    rules = RuleSet.from_strings(allow=["terminal.exec(npm run *)"])
    # both subcommands covered → allow
    assert _exec("npm run lint && npm run build", rules=rules) is Decision.ALLOW
    # second subcommand not covered → prompt
    assert _exec("npm run lint && rm foo", rules=rules) is Decision.ASK


def test_compound_allow_mixes_read_only_and_allowed() -> None:
    rules = RuleSet.from_strings(allow=["terminal.exec(npm run *)"])
    # `ls` is read-only, `npm run build` is allowed → whole line allowed
    assert _exec("ls && npm run build", rules=rules) is Decision.ALLOW


def test_deny_fires_on_any_subcommand() -> None:
    rules = RuleSet.from_strings(deny=["terminal.exec(rm *)"])
    assert _exec("npm run build && rm -rf node_modules", rules=rules) is Decision.DENY


def test_wrapper_stripped_before_matching() -> None:
    rules = RuleSet.from_strings(allow=["terminal.exec(npm test*)"])
    assert _exec("timeout 30 npm test", rules=rules) is Decision.ALLOW
    assert _exec("sudo npm test", rules=rules) is Decision.ALLOW
    assert _exec("nice -n 10 npm test", rules=rules) is Decision.ALLOW
    assert _exec("env FOO=bar npm test", rules=rules) is Decision.ALLOW


def test_pipe_splits_subcommands() -> None:
    rules = RuleSet.from_strings(allow=["terminal.exec(cat *)"])
    # cat is read-only and allowed; grep is read-only → allowed
    assert _exec("cat log | grep error", rules=rules) is Decision.ALLOW


def test_shell_circuit_breakers() -> None:
    assert _exec("mkfs.ext4 /dev/sda1", mode=Mode.AUTONOMOUS) is Decision.ASK
    assert _exec("dd if=/dev/zero of=/dev/sda", mode=Mode.AUTONOMOUS) is Decision.ASK
    assert _exec(":(){ :|:& };:", mode=Mode.AUTONOMOUS) is Decision.ASK


# --- stored-rule migration across a tool rename -----------------------------


def test_rename_moves_shared_notebook_verbs_to_training() -> None:
    from backend.modules.agent.permission_store import rename_in_rule

    # A `notebook.run_cell` grant was made when training's identically-named tool
    # was the one that actually ran; it must follow the tool, not the name.
    assert rename_in_rule("notebook.run_cell") == "training.run_cell"
    assert (
        rename_in_rule("notebook.insert_cell(proj-1)") == "training.insert_cell(proj-1)"
    )
    # Reactive-notebook-only verbs never collided, so they keep meaning what they said.
    assert rename_in_rule("notebook.set_mode") == "notebook.set_mode"
    # The stopgap name the collision forced rejoins its own group.
    assert rename_in_rule("nb.list_cells") == "notebook.list_cells"
    # Untouched tools pass through, specifier and all.
    assert rename_in_rule("terminal.exec(npm run *)") == "terminal.exec(npm run *)"


def test_rename_leaves_a_specifier_containing_dots_alone() -> None:
    from backend.modules.agent.permission_store import rename_in_rule

    # Only the head is a tool name; a specifier may contain anything.
    assert rename_in_rule("notebook.run_cell(a.b(c))") == "training.run_cell(a.b(c))"
    assert (
        rename_in_rule("files.read(notebook.run_cell)")
        == "files.read(notebook.run_cell)"
    )


def test_migration_rewrites_once_and_dedupes(monkeypatch) -> None:
    from backend.modules.agent import permission_store as PS

    store: dict[str, object] = {
        PS.KEY_ALLOW: ["notebook.run_cell", "training.run_cell", "terminal.exec"],
        PS.KEY_ASK: [],
        PS.KEY_DENY: [],
    }
    writes: list[str] = []
    monkeypatch.setattr(PS, "get_value", lambda k, d=None: store.get(k, d))

    def _set(key: str, value: object) -> None:
        store[key] = value
        writes.append(key)

    monkeypatch.setattr(PS, "set_value", _set)

    PS.load_rules()
    # The renamed rule collapses into the one that already used the new name.
    assert store[PS.KEY_ALLOW] == ["training.run_cell", "terminal.exec"]
    assert writes == [PS.KEY_ALLOW]

    # Idempotent: a second load is a no-op, so this costs nothing after the first.
    PS.load_rules()
    assert writes == [PS.KEY_ALLOW]


def test_shell_split_and_wrapper_units() -> None:
    from backend.modules.agent import shell

    assert shell.split_commands("a && b | c ; d") == ["a", "b", "c", "d"]
    assert shell.split_commands("echo 'a && b'") == ["echo 'a && b'"]
    assert shell.strip_wrappers("timeout 5 npm run build") == "npm run build"
    assert shell.command_name("/usr/bin/npm test") == "npm"
    assert shell.is_read_only("ls -la") is True
    assert shell.is_read_only("rm foo") is False
