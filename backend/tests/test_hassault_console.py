"""Unit tests for hAssault Developer Console, CVars, ConCommands, and Macros."""

import pytest

from backend.modules.hassault.console import (
    ConsoleExecRequest,
    ConsoleRegistry,
    console_registry,
)


@pytest.fixture
def fresh_registry() -> ConsoleRegistry:
    return ConsoleRegistry()


@pytest.mark.anyio
async def test_cvar_query_and_set(fresh_registry: ConsoleRegistry) -> None:
    # 1. Query CVar
    req_query = ConsoleExecRequest(command="net.graph")
    res_query = await fresh_registry.execute(req_query)
    assert res_query.ok is True
    assert res_query.result_data == 0
    assert any("net.graph" in line for line in res_query.output)

    # 2. Set CVar (Source style)
    req_set = ConsoleExecRequest(command="net.graph 2")
    res_set = await fresh_registry.execute(req_set)
    assert res_set.ok is True
    assert res_set.result_data == 2
    assert res_set.affected_cvars.get("net.graph") == 2
    assert fresh_registry.cvars["net.graph"].current_value == 2

    # 3. Set CVar (equal sign style)
    req_eq = ConsoleExecRequest(command="draw.wireframe = 1")
    res_eq = await fresh_registry.execute(req_eq)
    assert res_eq.ok is True
    assert res_eq.affected_cvars.get("draw.wireframe") is True
    assert fresh_registry.cvars["draw.wireframe"].current_value is True

    # 4. Bounds clamping
    req_clamp = ConsoleExecRequest(command="draw.fov 150")
    res_clamp = await fresh_registry.execute(req_clamp)
    assert res_clamp.ok is True
    # The ceiling is `settings::FOV_RANGE.1` in the native client, which reads
    # this same CVar — see `App::base_fov`. The two ranges have to agree or the
    # console sets a value the video menu cannot show.
    assert res_clamp.affected_cvars.get("draw.fov") == 120.0


@pytest.mark.anyio
async def test_command_execution(fresh_registry: ConsoleRegistry) -> None:
    # Test server.start
    req = ConsoleExecRequest(command='server.start map:hd_atrium bots:2 skill:hard')
    res = await fresh_registry.execute(req)
    assert res.ok is True
    assert res.result_data["map"] == "hd_atrium"
    assert len(res.result_data["bots"]) == 2
    assert res.result_data["skill"] == "hard"


@pytest.mark.anyio
async def test_chained_commands(fresh_registry: ConsoleRegistry) -> None:
    req = ConsoleExecRequest(command="server.cheats 1; draw.hitboxes 1; net.simulate_lag 50")
    res = await fresh_registry.execute(req)
    assert res.ok is True
    assert res.affected_cvars.get("server.cheats") is True
    assert res.affected_cvars.get("draw.hitboxes") is True
    assert res.affected_cvars.get("net.simulate_lag") == 50.0


@pytest.mark.anyio
async def test_python_script_execution(fresh_registry: ConsoleRegistry) -> None:
    code = (
        "server.cheats = True\n"
        "draw.hitboxes = True\n"
        "net.simulate_lag = 75\n"
        "print('Configured test arena!')"
    )
    req = ConsoleExecRequest(command=code)
    res = await fresh_registry.execute(req)
    assert res.ok is True
    assert res.affected_cvars.get("server.cheats") is True
    assert res.affected_cvars.get("draw.hitboxes") is True
    assert res.affected_cvars.get("net.simulate_lag") == 75
    assert any("Configured test arena!" in line for line in res.output)


@pytest.mark.anyio
async def test_macro_crud_and_run(fresh_registry: ConsoleRegistry) -> None:
    # 1. List macros
    req_list = ConsoleExecRequest(command="macro.list")
    res_list = await fresh_registry.execute(req_list)
    assert res_list.ok is True
    assert len(res_list.result_data) >= 3

    # 2. Run builtin warmup macro
    req_run = ConsoleExecRequest(command='macro.run("warmup")')
    res_run = await fresh_registry.execute(req_run)
    assert res_run.ok is True
    assert res_run.affected_cvars.get("server.cheats") is True
    assert res_run.affected_cvars.get("player.god") is True
    assert res_run.affected_cvars.get("player.infinite_ammo") is True
    assert res_run.affected_cvars.get("draw.hitboxes") is True

    # 3. Save custom user macro
    macro = fresh_registry.save_macro(
        name="test_drill",
        code="net.graph = 1; print('Drill active')",
        desc="Test drill",
    )
    assert macro.name == "test_drill"
    assert "test_drill" in fresh_registry.macros

    # 4. Run custom macro
    req_custom = ConsoleExecRequest(command='macro.run("test_drill")')
    res_custom = await fresh_registry.execute(req_custom)
    assert res_custom.ok is True

    # 5. Delete custom macro
    deleted = fresh_registry.delete_macro("test_drill")
    assert deleted is True
    assert "test_drill" not in fresh_registry.macros


@pytest.mark.anyio
async def test_help_and_search(fresh_registry: ConsoleRegistry) -> None:
    req = ConsoleExecRequest(command='help("hitbox")')
    res = await fresh_registry.execute(req)
    assert res.ok is True
    assert any("hitbox" in c["name"] for c in res.result_data["cvars"] + res.result_data["commands"])
