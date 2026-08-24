"""What a guest may actually *do*, and the two gates every action passes.

Phase 1 built the ladder and the door; this is what stands behind it. Every guest
action goes through the same sequence, in this order, with no way around it:

1. **The ladder** (`gate.require`) -- is this participant on a high enough rung?
2. **The host's own permission engine** (`agent/permissions.evaluate`) -- for the
   rungs that map onto tools the host's agent can also call.
3. **Actuation**, and only then.
4. **The audit log**, on every path including both refusals.

Step 2 is the invariant worth defending, and the reason this file is small and
declarative rather than a pile of `if` branches. `terminal` and `agent` grants do
**not** carry their own permission logic: they ask the same engine the host's own
agent asks, so a guest can never exceed what the host's own rules allow. A second,
laxer implementation of "may this run?" is exactly where the gap appears -- and it
would be an invisible gap, because the ladder check above it would still pass.

The registry is **deny by default**. An action name this build has never heard of
is refused rather than falling through to some permissive base case, which is what
makes it safe for a peer on a newer build to ask for something new.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from backend.modules.share.models import GrantLevel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionSpec:
    """One thing a guest can ask for."""

    #: The rung required. Compared by `gate.allows`, never by anything else.
    needs: GrantLevel
    #: The host-side tool name this maps onto, when it maps onto one. Set means
    #: the action is *also* run past the host's agent permission engine under
    #: this name, so the host's existing allow/deny/ask rules apply unchanged.
    tool: str | None = None
    #: Whether the action changes anything. Read-only actions are never gated by
    #: the permission engine (`evaluate` short-circuits on `side_effect`), which
    #: is correct and worth being explicit about here rather than implicit there.
    side_effect: bool = True
    #: Pulls the human-meaningful specifier out of the params -- the command for a
    #: terminal run, the path for an edit. This is what the host's rules match
    #: against and what the audit log shows, so a wrong one here means rules that
    #: silently never match.
    specifier: Callable[[dict[str, Any]], str | None] | None = None
    #: High-frequency actions (a moving cursor) are actuated but not written to
    #: the audit log, which would otherwise be nothing but cursor rows.
    audited: bool = True
    #: An additional existing gate this action must also satisfy, for capabilities
    #: that already have one somewhere else in the codebase. Returns
    #: `(ok, reason)`. Used rather than re-deriving the same policy here -- the
    #: remote-agent admission check is a rule this node already applies to
    #: `agent.ask_peer`, and a second copy of it would be a second thing to
    #: forget to update.
    extra_gate: Callable[[], tuple[bool, str]] | None = None


def _cmd(params: dict[str, Any]) -> str | None:
    value = params.get("command")
    return str(value) if isinstance(value, str) and value else None


def _path(params: dict[str, Any]) -> str | None:
    value = params.get("path") or params.get("uri")
    return str(value) if isinstance(value, str) and value else None


def _prompt(params: dict[str, Any]) -> str | None:
    value = params.get("prompt")
    return str(value)[:200] if isinstance(value, str) and value else None


def _remote_agent_admitted() -> tuple[bool, str]:
    """The node's existing admission rule for a remote agent turn.

    Reused rather than re-derived: this is exactly the check `agent.ask_peer`
    already passes on the receiving side (`network/agent_bridge.py`). A guest
    holding the `agent` rung on a node whose owner has not enabled remote agent
    access still gets nothing, which is the correct reading -- friendship grants
    reachability, not authority, and so does a session grant.
    """
    from backend.modules.settings.routes import get_value

    if not get_value("network.allowRemoteAgent", False):
        return False, "this node has remote agent access disabled"
    return True, ""


#: The whole vocabulary. Adding a row is how a capability becomes reachable by a
#: guest; there is deliberately no other way in.
REGISTRY: dict[str, ActionSpec] = {
    # -- cursor ---------------------------------------------------------------
    # A pointer position in a mirrored pane. Read-only by construction: it shows
    # the host where a guest is looking and touches nothing.
    "cursor.move": ActionSpec(needs="cursor", side_effect=False, audited=False),
    # Naming a pane to draw attention to it. Audited -- rare, and meaningful.
    "cursor.point": ActionSpec(needs="cursor", side_effect=False),
    # -- edit -----------------------------------------------------------------
    # Joining a shared pane's collab room. The *edits themselves* then ride the
    # room's existing rev/LWW protocol rather than passing through here one
    # keystroke at a time -- this is the authorization, not the transport.
    "edit.join": ActionSpec(needs="edit", tool="editor.applyEdit", specifier=_path),
    # -- terminal -------------------------------------------------------------
    # `terminal.exec`, NOT some share-local spelling. That name is in
    # `permissions.SHELL_TOOLS`, which is what selects shell-aware rule matching
    # (per-subcommand) and arms the `rm -rf` circuit breaker. A near-miss like
    # `terminal.run` would pass the ladder, find no matching host rule, skip the
    # breakers entirely, and read as "allowed" -- a silent hole with a plausible
    # name on it.
    "terminal.exec": ActionSpec(needs="terminal", tool="terminal.exec", specifier=_cmd),
    # -- agent ----------------------------------------------------------------
    # No tool mapping: a remote agent turn is not a tool call, it is the path
    # `network/agent_bridge.py` already owns and already gates. The rung gets you
    # to the door; that node's own remote-agent setting decides whether it opens.
    "agent.ask": ActionSpec(
        needs="agent", specifier=_prompt, extra_gate=_remote_agent_admitted
    ),
    # -- control --------------------------------------------------------------
    # The top rung: driving the host's layout. The tool names are the bare ones
    # the host's own agent uses (`open_pane`, not `layout.open_pane`), so a host
    # who has denied them to their agent has denied them to a guest too. A
    # prefixed spelling would match no rule at all.
    "control.openPane": ActionSpec(needs="control", tool="open_pane"),
    "control.closePane": ActionSpec(needs="control", tool="close_pane"),
}


def spec(action: str) -> ActionSpec | None:
    """The spec for an action name, or None if this build does not know it."""
    return REGISTRY.get(action)


def permission_decision(action: str, params: dict[str, Any]) -> tuple[str, str | None]:
    """Run an action past the **host's own** agent permission engine.

    Returns `(decision, specifier)` where decision is one of `allow`/`ask`/`deny`.
    An action that maps onto no tool is `allow` here -- the ladder was its only
    gate, which is the correct reading for something like a cursor position that
    the host's agent has no equivalent of.

    Note the mode and rules come from the host's live configuration, not from
    anything the guest sent. That is the whole point: the guest cannot influence
    the policy it is being judged by.
    """
    entry = REGISTRY.get(action)
    if entry is None or entry.tool is None:
        return "allow", None

    from backend.modules.agent import permission_store, permissions as perms

    specifier = entry.specifier(params) if entry.specifier else None
    decision = perms.evaluate(
        tool=entry.tool,
        specifier=specifier,
        side_effect=entry.side_effect,
        # The host's live mode and rules, read here and not passed in: a guest
        # must not be able to influence the policy it is judged by.
        mode=permission_store.load_mode(),
        rules=permission_store.load_rules(),
    )
    return str(decision.value), specifier
