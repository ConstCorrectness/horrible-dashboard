"""Persistence glue between the pure permission engine and the settings store.

Loads the session `Mode` and the `RuleSet` the orchestrator gates with, and
appends rules when the user picks "always allow". Rules and mode live in the
shared settings store under `agent.permissions.*` keys; the settings module owns
the editing UI (A6). See docs/architecture/agent-tools.md.
"""

from __future__ import annotations

from backend.modules.agent.permissions import Mode, RuleSet
from backend.modules.settings.routes import get_value, set_value

KEY_MODE = "agent.permissions.mode"
KEY_ALLOW = "agent.permissions.allow"
KEY_ASK = "agent.permissions.ask"
KEY_DENY = "agent.permissions.deny"

_LIST_KEYS = {"allow": KEY_ALLOW, "ask": KEY_ASK, "deny": KEY_DENY}


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


def load_rules() -> RuleSet:
    return RuleSet.from_strings(
        allow=_load_list(KEY_ALLOW),
        ask=_load_list(KEY_ASK),
        deny=_load_list(KEY_DENY),
    )


def add_rule(list_name: str, rule: str) -> None:
    """Append `rule` to a rule list (allow/ask/deny) if not already present."""
    key = _LIST_KEYS[list_name]
    rules = _load_list(key)
    if rule not in rules:
        rules.append(rule)
        set_value(key, rules)
