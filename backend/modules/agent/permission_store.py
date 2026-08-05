"""Persistence glue between the pure permission engine and the settings store.

Loads the session `Mode` and the `RuleSet` the orchestrator gates with, and
appends rules when the user picks "always allow". Rules and mode live in the
shared settings store under `agent.permissions.*` keys; the settings module owns
the editing UI (A6). See docs/architecture/agent-tools.md.
"""

from __future__ import annotations

import logging

from backend.modules.agent.permissions import Mode, RuleSet
from backend.modules.settings.routes import get_value, set_value

logger = logging.getLogger(__name__)

KEY_MODE = "agent.permissions.mode"
KEY_ALLOW = "agent.permissions.allow"
KEY_ASK = "agent.permissions.ask"
KEY_DENY = "agent.permissions.deny"

_LIST_KEYS = {"allow": KEY_ALLOW, "ask": KEY_ASK, "deny": KEY_DENY}

# Stored rules name a tool, so renaming a tool moves whatever the user decided
# about it. Both directions of this rename are load-bearing:
#
# The reactive notebook and the training notebook both declared these verbs as
# `notebook.*`; the frontend resolves a tool call with `find`, and training
# registers first, so a `notebook.run_cell` grant was in fact a grant over the
# *training* notebook. Leaving the rule alone would silently re-point it at the
# reactive notebook — a decision the user never made, applied to a different
# kernel. `set_mode` is untouched: it never collided, so it always meant the
# reactive notebook. `nb.list_cells` is the stopgap name the collision forced.
_RULE_RENAMES: dict[str, str] = {
    **{
        f"notebook.{verb}": f"training.{verb}"
        for verb in (
            "list_cells",
            "read_cell",
            "kernel_status",
            "insert_cell",
            "edit_cell",
            "delete_cell",
            "run_cell",
            "run_all",
            "interrupt",
            "restart",
        )
    },
    "nb.list_cells": "notebook.list_cells",
}


def load_mode() -> Mode:
    raw = get_value(KEY_MODE, Mode.DEFAULT.value)
    try:
        return Mode(raw)
    except ValueError:
        return Mode.DEFAULT


def _load_list(key: str) -> list[str]:
    value = get_value(key, [])
    if not isinstance(value, list):
        return []
    return [str(v) for v in value]


def rename_in_rule(rule: str) -> str:
    """Apply `_RULE_RENAMES` to a stored rule string.

    A rule is `tool` or `tool(specifier)`; only the tool part is rewritten, and a
    specifier is passed through untouched — it may contain anything, including
    dots and parentheses, so this splits on the *first* `(` rather than parsing.
    """
    head, sep, tail = rule.partition("(")
    renamed = _RULE_RENAMES.get(head.strip())
    return f"{renamed}{sep}{tail}" if renamed else rule


def _migrate_list(key: str) -> list[str]:
    """Load a rule list, rewriting renamed tool names in place (once).

    Runs on read rather than as a startup step because the settings store is the
    only thing that knows whether this install has any rules at all, and a rewrite
    that produces no change writes nothing — so this is idempotent and free after
    the first pass.
    """
    rules = _load_list(key)
    migrated: list[str] = []
    for rule in rules:
        new = rename_in_rule(rule)
        if new not in migrated:
            migrated.append(new)
    if migrated != rules:
        logger.info("agent permissions: migrated renamed tools in %s", key)
        set_value(key, migrated)
    return migrated


def load_rules() -> RuleSet:
    return RuleSet.from_strings(
        allow=_migrate_list(KEY_ALLOW),
        ask=_migrate_list(KEY_ASK),
        deny=_migrate_list(KEY_DENY),
    )


def add_rule(list_name: str, rule: str) -> None:
    """Append `rule` to a rule list (allow/ask/deny) if not already present."""
    key = _LIST_KEYS[list_name]
    rules = _load_list(key)
    if rule not in rules:
        rules.append(rule)
        set_value(key, rules)
