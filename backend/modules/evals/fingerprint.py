"""What the catalog under test actually looked like when a sweep ran.

An eval here measures **tool calling**, and two things quietly rewrite the tool
catalog between one run and the next without touching a single case:

* every **enabled skill** rides every turn — its description is in the catalog the
  model chooses from, and `use_skill` pastes its body into the turn;
* every **connected MCP server** contributes a whole tool group (`mcp-<id>.*`).

Toggle a skill or start a server between two runs of the same suite and Compare
will report fixes and regressions that were yours, not the model's. That is
exactly the failure `case_hash` exists to prevent one level down — there, "the same
suite" stopped meaning "the same questions"; here, "the same harness" stops meaning
"the same tools" — so it gets the same shape of fix: a content hash recorded
alongside the run, and a comparison that says *cannot tell* rather than guessing.

Three properties do the work:

**Content, not names.** A skill whose body was rewritten is a different harness
even though the catalog lists the same name, because `use_skill` pastes that body
into the turn. Same for an MCP tool whose schema changed under a stable name.

**Run level, not case level.** The harness is a property of the sweep — every case
in a run saw the same one — so it belongs on `eval_runs` and surfaces as a banner
over a comparison, not as a fifth column on every row.

**An empty hash means "cannot tell", never "the same".** Runs recorded before this
column existed have no hash, and so does a run whose harness could not be read.
Both must read as unknown; treating an absent hash as agreement is the misreading
this module was written to stop.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _sha(payload: Any) -> str:
    """A short content hash over canonical JSON.

    Canonical for the same reason the peer wire is: the hash has to survive a
    restart, a dict that iterates in a different order, and a re-read of the same
    files, or every second run would report a differing harness and the banner
    would become noise people learn to ignore.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _skills() -> list[dict[str, str]]:
    """The enabled, usable skills — the ones that actually reach the model."""
    from backend.modules.skills import store as skills

    return sorted(
        (
            {
                "name": skill.name,
                "hash": _sha(
                    {
                        "description": skill.description,
                        "body": skill.body,
                        "allowedTools": sorted(skill.allowed_tools),
                    }
                ),
            }
            for skill in skills.active_skills()
        ),
        key=lambda s: s["name"],
    )


def _mcp() -> list[dict[str, Any]]:
    """The connected MCP servers and the tools they are currently bridging.

    Read from the live manager rather than the config file: a server that is
    configured but failed to start contributes nothing to the catalog, and
    recording it would make a run that saw no tools look like one that did.
    """
    from backend.modules.mcp.client import manager

    servers: list[dict[str, Any]] = []
    for runtime in manager.runtimes():
        if runtime.state != "ready":
            continue
        servers.append(
            {
                "id": runtime.id,
                "group": runtime.group,
                "tools": sorted(
                    (
                        {
                            "name": tool.name,
                            "hash": _sha(
                                {
                                    "description": tool.description,
                                    "schema": tool.input_schema,
                                    "readOnly": tool.read_only,
                                }
                            ),
                        }
                        for tool in runtime.tools
                    ),
                    key=lambda t: t["name"],
                ),
            }
        )
    return sorted(servers, key=lambda s: s["id"])


def compute() -> tuple[str, str]:
    """`(hash, json)` for the harness this process is currently offering.

    Both empty when it could not be read. Nothing here is worth failing a sweep
    over — losing the results of a twenty-minute run because a skill file was
    unreadable would be a bad trade — so the whole thing is wrapped and a failure
    degrades the comparison to "cannot tell", which is what it honestly is.
    """
    try:
        harness = {"skills": _skills(), "mcp": _mcp()}
    except Exception:  # noqa: BLE001
        logger.debug("evals: could not read the harness", exc_info=True)
        return "", ""
    return _sha(harness), json.dumps(harness, sort_keys=True, separators=(",", ":"))


def _index(blob: str) -> tuple[dict[str, str], dict[str, str]] | None:
    """`({skill: hash}, {mcp tool: hash})` for one recorded harness, or None."""
    try:
        data = json.loads(blob) if blob else None
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    skills = {
        str(s.get("name", "")): str(s.get("hash", "")) for s in data.get("skills", [])
    }
    tools: dict[str, str] = {}
    for server in data.get("mcp", []):
        group = str(server.get("group") or server.get("id", ""))
        for tool in server.get("tools", []):
            tools[f"{group}.{tool.get('name', '')}"] = str(tool.get("hash", ""))
    return skills, tools


def _diff_maps(kind: str, before: dict[str, str], after: dict[str, str]) -> list[str]:
    lines = [f"{kind} {name} was added" for name in sorted(set(after) - set(before))]
    lines += [f"{kind} {name} was removed" for name in sorted(set(before) - set(after))]
    lines += [
        f"{kind} {name} changed"
        for name in sorted(set(before) & set(after))
        if before[name] != after[name]
    ]
    return lines


def describe_difference(base_json: str, other_json: str) -> list[str]:
    """Plain lines naming what changed between two recorded harnesses.

    Empty when nothing changed *or* when either side is unreadable — the caller
    already knows the hashes differ, and inventing a reason from a blob that would
    not parse is worse than saying only that they differ.
    """
    before, after = _index(base_json), _index(other_json)
    if before is None or after is None:
        return []
    return _diff_maps("skill", before[0], after[0]) + _diff_maps(
        "MCP tool", before[1], after[1]
    )
