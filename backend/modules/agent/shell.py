"""Shell command analysis for the permission engine (A4b).

Pure string utilities — no dependency on `permissions.py`, so it imports one way.
Ports Claude Code's Bash/PowerShell rule logic: split a command into the
subcommands a rule must each cover, strip leading wrappers (`timeout`, `nice`, …)
before matching, recognize an always-safe read-only allowlist, and flag
universally-destructive commands for the circuit breaker. See
docs/architecture/agent-tools.md.
"""

from __future__ import annotations

import re

# Commands that only read state — run without a prompt in every mode. Conservative
# on purpose: multi-verb tools (git, npm, docker) are excluded since some of their
# subcommands mutate. A command redirecting output (`>`/`>>`) is never read-only.
READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "cat",
        "pwd",
        "echo",
        "head",
        "tail",
        "wc",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "find",
        "which",
        "type",
        "file",
        "stat",
        "df",
        "du",
        "date",
        "whoami",
        "id",
        "hostname",
        "uname",
        "printenv",
        "tree",
        "less",
        "more",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "true",
        "false",
        "sleep",
    }
)

# Leading tokens that wrap another command; stripped before matching so a rule for
# the real command still applies (e.g. `timeout 30 npm test` → `npm test`).
_WRAPPERS: frozenset[str] = frozenset(
    {"sudo", "nohup", "time", "command", "builtin", "stdbuf", "xargs"}
)


def split_commands(command: str) -> list[str]:
    """Split a command line into subcommands on `&& || ; | &`, respecting quotes.
    A permission rule must independently cover each returned subcommand."""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if command[i : i + 2] in ("&&", "||", ";;"):
            parts.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "|", "&"):
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def strip_wrappers(subcommand: str) -> str:
    """Remove leading wrapper tokens (and their args) so matching sees the real
    command. Handles `env VAR=val…`, `timeout [opts] DURATION`, `nice [-n N]`, and
    the bare wrappers in `_WRAPPERS`."""
    tokens = subcommand.split()
    while tokens:
        head = tokens[0]
        if head == "env":
            tokens = tokens[1:]
            while tokens and "=" in tokens[0] and not tokens[0].startswith("-"):
                tokens = tokens[1:]
            continue
        if head == "timeout":
            tokens = tokens[1:]
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[1:]
            if tokens:  # the duration argument
                tokens = tokens[1:]
            continue
        if head == "nice":
            tokens = tokens[1:]
            if tokens and tokens[0] == "-n" and len(tokens) > 1:
                tokens = tokens[2:]
            elif tokens and tokens[0].startswith("-"):
                tokens = tokens[1:]
            continue
        if head in _WRAPPERS:
            tokens = tokens[1:]
            continue
        break
    return " ".join(tokens)


def command_name(subcommand: str) -> str:
    """The program name of a (wrapper-stripped) subcommand, path stripped."""
    stripped = strip_wrappers(subcommand)
    if not stripped:
        return ""
    first = stripped.split()[0].strip("'\"")
    return first.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def is_read_only(subcommand: str) -> bool:
    """Whether a subcommand only reads state (safe to run without a prompt). A
    redirection makes it a writer, so those are excluded."""
    if ">" in subcommand:
        return False
    return command_name(subcommand) in READ_ONLY_COMMANDS


# Universally-destructive patterns that always force a prompt (the shell circuit
# breakers). `rm -rf` of a root/home/wildcard is handled by the core engine's
# default breaker; these cover the rest.
_DANGEROUS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\b.*\bof=/dev/", re.IGNORECASE),
    re.compile(r":\(\)\s*\{.*:\|:", re.DOTALL),  # fork bomb
    re.compile(r">\s*/dev/sd[a-z]"),
    re.compile(r"\bchmod\b.*-R.*\s/(?:\s|$)", re.IGNORECASE),
)


def is_dangerous(command: str) -> bool:
    """Whether the command line trips a shell circuit breaker."""
    return any(pat.search(command) for pat in _DANGEROUS)
