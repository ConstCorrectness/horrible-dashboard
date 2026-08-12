"""The conformance suite, run against a compliant server and a broken one.

Both halves are necessary and neither is sufficient. Against the compliant fixture the
suite must come back clean — a suite that flags healthy servers is one nobody reads.
Against `mcp_broken_server.py` it must actually catch the contradiction — a suite that
passes everything is indistinguishable from one that was never implemented, which is
the failure mode of every checklist.

The schema checks are asserted against synthetic runtimes rather than a fixture,
because the malformed schemas that matter (a `required` naming a property that doesn't
exist) cannot be produced through FastMCP at all — it derives the schema from the
signature. A server hand-rolling JSON-RPC absolutely can, and that is exactly who needs
telling.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from backend.modules.mcp import conformance
from backend.modules.mcp.client import McpSession, ServerRuntime, ToolInfo

FIXTURE_SERVER = str(Path(__file__).parent / "mcp_fixture_server.py")
BROKEN_SERVER = str(Path(__file__).parent / "mcp_broken_server.py")


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return tmp_path


def _config(server_id: str, script: str) -> dict[str, Any]:
    import sys

    return {
        "id": server_id,
        "transport": "stdio",
        "command": sys.executable,
        "args": [script],
        "env": {},
    }


def _run_suite(server_id: str, script: str) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        session = McpSession(_config(server_id, script))
        try:
            await session.start()
            assert session.runtime.state == "ready", session.runtime.error
            return await conformance.run(session)
        finally:
            await session.stop()

    return asyncio.run(go())


def _by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in report["checks"]}


# --- against a compliant server -----------------------------------------------


def test_a_compliant_server_produces_no_failures(data_dir: Path):
    report = _run_suite("fixture", FIXTURE_SERVER)
    checks = _by_id(report)
    assert [c["id"] for c in report["checks"] if c["status"] == "fail"] == []
    assert checks["handshake"]["status"] == "pass"
    assert checks["capabilities"]["status"] == "pass"
    assert checks["schemas"]["status"] == "pass"


def test_the_handshake_check_reports_what_it_negotiated(data_dir: Path):
    """A protocol version is the one piece of the handshake with no other surface."""
    report = _run_suite("fixture", FIXTURE_SERVER)
    assert report["protocolVersion"]
    assert report["serverName"] == "fixture"
    assert report["protocolVersion"] in _by_id(report)["handshake"]["detail"]


def test_an_impossible_tool_call_is_rejected_not_hung(data_dir: Path):
    check = _by_id(_run_suite("fixture", FIXTURE_SERVER))["unknown-tool"]
    assert check["status"] == "pass"
    assert "timed out" not in check["detail"]


def test_the_suite_never_calls_a_write_tool(data_dir: Path):
    """`poke` writes. The one check that calls something real aims only at a tool the
    server annotated read-only, and skips when there isn't one — a conformance run
    whose side effect was mutating state would be unusable."""

    async def go() -> tuple[dict[str, Any], list[str]]:
        session = McpSession(_config("fixture", FIXTURE_SERVER))
        called: list[str] = []
        original = session.call_tool

        async def spy(tool: str, args: dict[str, Any]) -> dict[str, Any]:
            called.append(tool)
            return await original(tool, args)

        try:
            await session.start()
            session.call_tool = spy  # type: ignore[method-assign]
            return await conformance.run(session), called
        finally:
            await session.stop()

    report, called = asyncio.run(go())
    assert "poke" not in called
    assert "boom" not in called
    # `peek` is annotated read-only and takes a required `key`, so it is the legal
    # target for the missing-arguments check — called with *no* arguments.
    assert called == [conformance._IMPOSSIBLE_TOOL, "peek"]
    assert _by_id(report)["missing-arguments"]["status"] == "pass"


# --- against a deliberately broken server -------------------------------------


def test_contradictory_annotations_are_a_failure(data_dir: Path):
    """readOnlyHint + destructiveHint on one tool. The dashboard trusts the first, so
    a server declaring both is asking for an unprompted call to a destructive tool."""
    report = _run_suite("broken", BROKEN_SERVER)
    check = _by_id(report)["annotations"]
    assert check["status"] == "fail"
    assert "wipe" in check["detail"]
    assert report["status"] == "fail"


def test_a_missing_description_is_flagged(data_dir: Path):
    check = _by_id(_run_suite("broken", BROKEN_SERVER))["schemas"]
    assert check["status"] == "warn"
    assert "undocumented" in check["detail"]


def test_a_server_with_no_documentation_is_flagged(data_dir: Path):
    """No `instructions` and no prompts means the group loads with nothing telling the
    model how to drive it — the cheapest quality win MCP offers, unspent."""
    check = _by_id(_run_suite("broken", BROKEN_SERVER))["guide"]
    assert check["status"] == "warn"


def test_the_missing_argument_check_skips_when_nothing_is_read_only(data_dir: Path):
    """The broken server's only read-only tool is the contradictory one — and it is
    still annotated read-only, so it remains the legal target. What must never happen
    is the check quietly falling back to a write tool."""
    report = _run_suite("broken", BROKEN_SERVER)
    check = _by_id(report)["missing-arguments"]
    assert check["status"] in ("pass", "skip", "warn")
    assert "undocumented" not in check["detail"]


# --- schema checks, against runtimes FastMCP cannot produce --------------------


class _StubSession:
    """Just enough of `McpSession` for the pure checks: a runtime."""

    def __init__(self, runtime: ServerRuntime) -> None:
        self.runtime = runtime


def _runtime(tools: list[ToolInfo], **kwargs: Any) -> ServerRuntime:
    runtime = ServerRuntime(config={"id": "x"}, tools=tools, **kwargs)
    runtime.state = "ready"
    return runtime


def _tool(name: str, schema: dict[str, Any], **kwargs: Any) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=kwargs.pop("description", "d"),
        input_schema=schema,
        **kwargs,
    )


def test_a_required_argument_with_no_property_is_a_failure():
    """The worst schema bug to ship: the model dutifully sends a field nothing
    documents, and the server rejects its own contract."""
    session = _StubSession(
        _runtime(
            [
                _tool(
                    "t",
                    {
                        "type": "object",
                        "properties": {"a": {"type": "string"}},
                        "required": ["a", "b"],
                    },
                )
            ]
        )
    )
    check = conformance.check_tool_schemas(session)  # type: ignore[arg-type]
    assert check.status == "fail"
    assert "b" in check.detail


def test_a_non_object_schema_is_a_failure():
    session = _StubSession(_runtime([_tool("t", {"type": "string"})]))
    check = conformance.check_tool_schemas(session)  # type: ignore[arg-type]
    assert check.status == "fail"


def test_a_name_the_bridge_would_rewrite_is_flagged():
    """`bridge.tool_name` substitutes illegal characters, so the model sees a name that
    doesn't match the server's own documentation."""
    session = _StubSession(
        _runtime([_tool("has spaces", {"type": "object", "properties": {}})])
    )
    check = conformance.check_tool_schemas(session)  # type: ignore[arg-type]
    assert check.status == "warn"
    assert "rewritten" in check.detail


def test_serving_tools_without_declaring_the_capability_is_a_failure():
    """It works here — we call `tools/list` when the capability is present — but a
    client that trusted the declaration would show an empty server."""
    runtime = _runtime([_tool("t", {"type": "object", "properties": {}})])
    runtime.capabilities = {}
    check = conformance.check_capabilities(_StubSession(runtime))  # type: ignore[arg-type]
    assert check.status == "fail"
    assert "tools" in check.detail


def test_a_scheme_less_resource_uri_is_a_failure():
    from backend.modules.mcp.client import ResourceInfo

    runtime = _runtime([])
    runtime.resources = [ResourceInfo(uri="/some/path", name="n", description="")]
    check = conformance.check_resource_uris(_StubSession(runtime))  # type: ignore[arg-type]
    assert check.status == "fail"


def test_an_unannotated_tool_is_a_warning_not_a_failure():
    """Unannotated is the safe state — it is gated. Saying `fail` would train the user
    to annotate everything read-only to clear the report, which is the opposite of
    what the check is for."""
    runtime = _runtime([_tool("t", {"type": "object", "properties": {}})])
    check = conformance.check_annotations(_StubSession(runtime))  # type: ignore[arg-type]
    assert check.status == "warn"
    assert "gated" in check.detail
