"""Permission engine for agent tool calls.

A direct port of Claude Code's model, scoped to this app's tools. The engine is
pure (no I/O): it evaluates a side-effecting tool call against a `RuleSet` and a
`Mode` and returns a `Decision`. Persistence (settings store) and the approval
round-trip live in later slices (A5/A6); shell-aware specifier matching and the
shell circuit breakers live in A4b, which plugs into the seams here.

Precedence is **deny → ask → allow**, first match wins; specificity does not
reorder. A matching `ask` prompts even when a more specific `allow` also matches.
See docs/architecture/agent-tools.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.modules.agent import shell

# Tools whose specifier is a shell command line, matched with shell-aware
# semantics (compound splitting, wrapper stripping, read-only allowlist) rather
# than a single glob. See shell.py / A4b.
SHELL_TOOLS: frozenset[str] = frozenset({"terminal.exec"})


class Mode(str, Enum):
    """Session permission mode, mirroring Claude Code."""

    DEFAULT = "default"  # prompt on every side effect not covered by an allow rule
    PLAN = "plan"  # read-only: every side effect is auto-denied
    ACCEPT_EDITS = (
        "acceptEdits"  # auto-allow safe edits/creation; still ask on delete/rename/exec
    )
    AUTONOMOUS = (
        "autonomous"  # allow everything except explicit ask/deny + circuit breakers
    )


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


# Tools auto-allowed under ACCEPT_EDITS (editor saves + safe filesystem creation).
# Deliberately excludes destructive verbs (delete/rename) and shell exec, which
# keep prompting. This is the v1 policy; refine as modules land.
EDIT_SAFE_TOOLS: frozenset[str] = frozenset(
    {"editor.save", "editor.applyEdit", "files.create", "files.write", "files.mkdir"}
)


def _glob_match(pattern: str, value: str) -> bool:
    """Match `value` against a `*`-glob `pattern`, anchored at both ends. `*`
    matches any run of characters (including none). A4b overrides specifier
    matching for shell tools with word-boundary semantics."""
    regex = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(regex, value, re.DOTALL) is not None


@dataclass(frozen=True)
class Rule:
    """A parsed permission rule: a bare tool name (matches every use of the tool)
    or `Tool(specifier)` (matches only calls whose rendered specifier matches the
    `*`-glob `specifier`). The tool name itself may also contain `*` (e.g.
    `files.*`)."""

    tool: str
    specifier: str | None = None

    @classmethod
    def parse(cls, raw: str) -> Rule:
        raw = raw.strip()
        match = re.fullmatch(r"([^(]+)\((.*)\)", raw, re.DOTALL)
        if match:
            return cls(match.group(1).strip(), match.group(2))
        return cls(raw, None)

    def matches(self, tool: str, specifier: str | None) -> bool:
        if not _glob_match(self.tool, tool):
            return False
        if self.specifier is None:
            return True  # bare tool name matches any use
        if specifier is None:
            return False  # rule wants a specifier but the call has none
        return _glob_match(self.specifier, specifier)


@dataclass
class RuleSet:
    allow: list[Rule] = field(default_factory=list)
    ask: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    @classmethod
    def from_strings(
        cls,
        allow: list[str] | None = None,
        ask: list[str] | None = None,
        deny: list[str] | None = None,
    ) -> RuleSet:
        return cls(
            allow=[Rule.parse(r) for r in (allow or [])],
            ask=[Rule.parse(r) for r in (ask or [])],
            deny=[Rule.parse(r) for r in (deny or [])],
        )


def _subcommands(specifier: str) -> list[str]:
    """The wrapper-stripped subcommands a shell specifier decomposes into."""
    return [shell.strip_wrappers(c) for c in shell.split_commands(specifier)]


def _rule_covers(rule: Rule, tool: str, sub: str) -> bool:
    """Whether `rule` covers a single (sub)command string."""
    if not _glob_match(rule.tool, tool):
        return False
    if rule.specifier is None:
        return True
    return _glob_match(rule.specifier, sub)


def _triggers(rule: Rule, tool: str, specifier: str | None) -> bool:
    """Whether `rule` fires for a call — used for deny/ask. For a shell tool a
    scoped rule fires if it matches *any* subcommand (so a dangerous part of a
    compound command can't hide behind a safe one)."""
    if tool not in SHELL_TOOLS or rule.specifier is None or specifier is None:
        return rule.matches(tool, specifier)
    if not _glob_match(rule.tool, tool):
        return False
    return any(_glob_match(rule.specifier, sub) for sub in _subcommands(specifier))


def _matches_any(rules: list[Rule], tool: str, specifier: str | None) -> bool:
    return any(_triggers(r, tool, specifier) for r in rules)


def _allowed_by(rules: list[Rule], tool: str, specifier: str | None) -> bool:
    """Whether the allow list covers the whole call. For a shell tool *every*
    subcommand must be covered by some allow rule (or be read-only); otherwise a
    single matching rule suffices."""
    if tool not in SHELL_TOOLS or specifier is None:
        return any(r.matches(tool, specifier) for r in rules)
    return all(
        shell.is_read_only(sub) or any(_rule_covers(r, tool, sub) for r in rules)
        for sub in _subcommands(specifier)
    )


def _auto_allow_read_only(tool: str, specifier: str | None) -> bool:
    """Shell read-only allowlist: a command whose every subcommand only reads
    state runs without a prompt in every mode."""
    if tool not in SHELL_TOOLS or specifier is None:
        return False
    subs = _subcommands(specifier)
    return bool(subs) and all(shell.is_read_only(sub) for sub in subs)


# Circuit breakers: always force a prompt, even in AUTONOMOUS / ACCEPT_EDITS, as a
# guard against model error. A breaker is a predicate over (tool, specifier).
# Seeded with one universally-destructive shell pattern; A4b registers the
# shell-aware ones and a filesystem one anchored on the workspace roots.
CircuitBreaker = Callable[[str, str | None], bool]

_RM_RF_ROOT = re.compile(r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+.*?(/|~|\*)", re.IGNORECASE)


def _rm_rf_breaker(tool: str, specifier: str | None) -> bool:
    return (
        tool == "terminal.exec"
        and specifier is not None
        and bool(_RM_RF_ROOT.search(specifier))
    )


def _shell_breaker(tool: str, specifier: str | None) -> bool:
    """Trip on universally-destructive shell commands (mkfs, dd of=/dev/, fork
    bombs, …). Scans the whole command line — some patterns (a fork bomb) span the
    operators `split_commands` would cut on. `rm -rf` of a root is the separate
    default breaker."""
    if tool not in SHELL_TOOLS or specifier is None:
        return False
    return shell.is_dangerous(specifier)


_circuit_breakers: list[CircuitBreaker] = [_rm_rf_breaker, _shell_breaker]


def register_circuit_breaker(breaker: CircuitBreaker) -> None:
    """Add a circuit breaker. A4b uses this to register shell/filesystem guards
    without this module depending on them."""
    _circuit_breakers.append(breaker)


def is_circuit_breaker(tool: str, specifier: str | None) -> bool:
    return any(breaker(tool, specifier) for breaker in _circuit_breakers)


def render_specifier(template: str | None, args: Mapping[str, Any]) -> str | None:
    """Render an `AgentToolDecl.specifierTemplate` into the specifier string the
    engine matches rule specifiers against, filling `{name}` placeholders from the
    call args. The tool name is implicit (never part of the template). Returns
    `None` when there is no template — the call is matched by bare tool name only."""
    if not template:
        return None

    def repl(match: re.Match[str]) -> str:
        value = args.get(match.group(1))
        return "" if value is None else str(value)

    return re.sub(r"\{(\w+)\}", repl, template)


def evaluate(
    tool: str,
    specifier: str | None,
    side_effect: bool,
    mode: Mode,
    rules: RuleSet,
) -> Decision:
    """Decide whether a tool call may run. Order:

    1. an explicit `deny` rule (strongest — beats everything, incl. AUTONOMOUS)
    2. a circuit breaker → ASK (beats allow rules and auto-allow modes)
    3. an explicit `ask` rule → ASK
    4. read-only tools (`side_effect` falsy) → ALLOW (never gated)
    5. a shell command whose every subcommand is read-only → ALLOW (every mode)
    6. mode auto-rules: PLAN → DENY, AUTONOMOUS → ALLOW, ACCEPT_EDITS safe edit → ALLOW
    7. an explicit `allow` rule → ALLOW (for a shell tool, all subcommands covered)
    8. otherwise → ASK (the DEFAULT-mode prompt)

    Shell-command specifiers (SHELL_TOOLS) match with shell-aware semantics; see
    shell.py.
    """
    if _matches_any(rules.deny, tool, specifier):
        return Decision.DENY
    if is_circuit_breaker(tool, specifier):
        return Decision.ASK
    if _matches_any(rules.ask, tool, specifier):
        return Decision.ASK
    if not side_effect:
        return Decision.ALLOW
    if _auto_allow_read_only(tool, specifier):
        return Decision.ALLOW
    if mode is Mode.PLAN:
        return Decision.DENY
    if mode is Mode.AUTONOMOUS:
        return Decision.ALLOW
    if mode is Mode.ACCEPT_EDITS and tool in EDIT_SAFE_TOOLS:
        return Decision.ALLOW
    if _allowed_by(rules.allow, tool, specifier):
        return Decision.ALLOW
    return Decision.ASK
