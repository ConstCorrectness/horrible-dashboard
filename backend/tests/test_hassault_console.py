"""Unit tests for hAssault Developer Console, CVars, ConCommands, and Macros."""

import pytest

from backend.modules.hassault import weapons
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
    req = ConsoleExecRequest(command="server.start map:hd_atrium bots:2 skill:hard")
    res = await fresh_registry.execute(req)
    assert res.ok is True
    assert res.result_data["map"] == "hd_atrium"
    assert len(res.result_data["bots"]) == 2
    assert res.result_data["skill"] == "hard"


@pytest.mark.anyio
async def test_chained_commands(fresh_registry: ConsoleRegistry) -> None:
    req = ConsoleExecRequest(
        command="server.cheats 1; draw.hitboxes 1; net.simulate_lag 50"
    )
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
    assert any(
        "hitbox" in c["name"]
        for c in res.result_data["cvars"] + res.result_data["commands"]
    )


# ---- player.give ------------------------------------------------------------------
#
# This command was dead from the day it was written: it read a
# `weapons.WEAPON_NAMES` that has never existed, so every invocation raised
# `AttributeError`. Nothing caught it because nothing ran it — hence these.


@pytest.fixture
def room_with_player():
    """A match with one human in it, so the ammo path is actually exercised.

    `resolve_player` returns the first **human**, so a bot would not do: with
    only bots in the room the command takes its client-side-predicted branch and
    never touches an inventory, which is precisely the half most worth pinning.
    """
    from backend.modules.hassault import match
    from backend.modules.hassault.physics import PlayerState

    room = match.match_server.create("hd_pit")
    player = match.MatchPlayer(
        id="p1", name="tester", team=0, state=PlayerState(x=8.0, y=8.0, z=0.0)
    )
    room.players[player.id] = player
    try:
        yield room, player
    finally:
        match.match_server.rooms.pop(room.id, None)


async def give(registry: ConsoleRegistry, room_id: str, weapon: str):
    return await registry.execute(
        ConsoleExecRequest(command=f"player.give {weapon}", room_id=room_id)
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query,slot",
    [
        ("knife", 0),
        ("pistol", 1),
        ("assault", 2),
        ("shotgun", 3),
        ("sniper", 4),
        # The display name, because that is what the HUD shows a player and so
        # what they are most likely to type.
        ("'Sniper Rifle'", 4),
        ("ASSAULT", 2),
    ],
)
async def test_give_by_name(
    fresh_registry: ConsoleRegistry, room_with_player, query, slot
) -> None:
    room, player = room_with_player
    result = await give(fresh_registry, room.id, query)
    assert result.ok, result.error
    assert result.result_data["id"] == slot
    assert player.weapon == slot


@pytest.mark.anyio
@pytest.mark.parametrize("slot", [0, 1, 2, 3, 4])
async def test_give_by_slot_number(
    fresh_registry: ConsoleRegistry, room_with_player, slot
) -> None:
    room, player = room_with_player
    result = await give(fresh_registry, room.id, str(slot))
    assert result.ok, result.error
    assert player.weapon == slot
    assert result.result_data["weapon"] == weapons.WEAPONS[slot].name


@pytest.mark.anyio
async def test_give_slot_zero_is_the_knife_not_the_default(
    fresh_registry: ConsoleRegistry, room_with_player
) -> None:
    """Slot 0 is falsy. The handler resolves its argument with `is None` rather
    than truthiness, because `0 or "assault"` is `"assault"` — which handed out
    the default rifle to anyone who asked for the knife by number."""
    room, player = room_with_player
    player.weapon = 3
    result = await give(fresh_registry, room.id, "0")
    assert result.ok, result.error
    assert player.weapon == 0
    assert result.result_data["weapon"] == weapons.WEAPONS[0].name


@pytest.mark.anyio
async def test_give_fills_the_magazine_and_the_reserve(
    fresh_registry: ConsoleRegistry, room_with_player
) -> None:
    """The point of the command. The numbers come from the served weapon table,
    so a change there cannot leave this handing out a stale magazine."""
    room, player = room_with_player
    result = await give(fresh_registry, room.id, "sniper")
    assert result.ok, result.error
    spec = weapons.weapon_at(4)
    assert player.ammo[4] == spec.mag
    assert player.reserve[4] == spec.reserve


@pytest.mark.anyio
@pytest.mark.parametrize("query", ["ak47", "carbine", "subgun", "9", "-1"])
async def test_give_refuses_a_weapon_that_does_not_exist(
    fresh_registry: ConsoleRegistry, room_with_player, query
) -> None:
    """`carbine` and `subgun` are here on purpose: the command used to *advertise*
    both, and neither has ever been a weapon in this game.

    An out-of-range slot is refused rather than clamped. `weapon_at` clamps
    because a bad slot arriving on the wire is a typo that must not drop a whole
    input frame; a person typing one at a console wants to be told.
    """
    room, player = room_with_player
    before = player.weapon
    result = await give(fresh_registry, room.id, query)
    assert not result.ok
    assert "unknown weapon" in (result.error or "")
    # The error names what would have worked.
    assert "sniper" in (result.error or "")
    assert player.weapon == before


@pytest.mark.anyio
async def test_give_defaults_to_a_weapon_that_exists(
    fresh_registry: ConsoleRegistry, room_with_player
) -> None:
    """The bare command used to default to `"carbine"`, which would have failed
    the lookup even once the lookup worked."""
    room, player = room_with_player
    for empty in ("", "''"):
        result = await give(fresh_registry, room.id, empty)
        assert result.ok, result.error
        assert player.weapon == weapons.DEFAULT_WEAPON


def test_the_command_only_advertises_weapons_that_exist() -> None:
    """The registry is **served** — `GET /console/definitions` hands this list to
    the native client and the browser console, and both render it as
    autocomplete. The hardcoded list this replaced offered two weapons the node
    has never had and omitted one it does.
    """
    definition = console_registry.commands["player.give"]
    advertised = definition.parameters[0].enum_values
    assert advertised == [w.id for w in weapons.WEAPONS]


def test_resolve_slot_accepts_the_three_things_a_person_types() -> None:
    assert weapons.resolve_slot("sniper") == 4
    assert weapons.resolve_slot("Sniper Rifle") == 4
    assert weapons.resolve_slot("4") == 4
    assert weapons.resolve_slot("ak47") is None
    assert weapons.resolve_slot(str(len(weapons.WEAPONS))) is None
    assert weapons.resolve_slot("") is None
