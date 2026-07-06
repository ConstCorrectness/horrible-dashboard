"""AgentTown tests: the world (join/act/tick/sleep), observation locality, and
the node-side resident mind (routine + agent mode + whisper)."""

from __future__ import annotations

import asyncio
import random
from typing import Any

from backend.games_server import models
from backend.games_server.hub import GameHub
from backend.games_server.town import ACTIONS, PLACES, SAY_MAX_CHARS, SPAWN
from backend.modules.games.town_policy import TownPolicy


class FakeConn:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.messages.append(msg)

    def last(self, mtype: str) -> dict[str, Any] | None:
        for msg in reversed(self.messages):
            if msg.get("type") == mtype:
                return msg
        return None


async def _resident(hub: GameHub, token: str, name: str = "", avatar: str = ""):
    conn = FakeConn()
    session = hub.connect(conn)
    await hub.handle(session, {"type": models.AUTH, "token": token})
    await hub.handle(
        session, {"type": models.TOWN_JOIN, "name": name, "avatar": avatar}
    )
    return conn, session


def _act(action: str, **kw: Any) -> dict[str, Any]:
    return {"type": models.TOWN_ACT, "action": action, **kw}


# ---- world -----------------------------------------------------------------


def test_join_spawns_at_fountain_with_immediate_tick() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        conn, _ = await _resident(hub, "alice", name="Mildred", avatar="🐙")
        joined = conn.last(models.TOWN_JOINED)
        assert joined is not None
        assert joined["resident"]["name"] == "Mildred"
        assert joined["resident"]["place"] == SPAWN
        # A fresh fish is prompted right away, not left waiting a whole tick.
        tick = conn.last(models.TOWN_TICK)
        assert tick is not None and tick["you"]["name"] == "Mildred"
        assert tick["places"] == list(PLACES)

    asyncio.run(go())


def test_one_resident_per_account_rejoin_takes_control() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        await _resident(hub, "alice", name="Mildred")
        conn2, _ = await _resident(hub, "alice", name="Mildred II")
        assert len(hub.town._residents) == 1
        assert conn2.last(models.TOWN_JOINED)["resident"]["name"] == "Mildred II"

    asyncio.run(go())


def test_tick_applies_move_then_say_lands_in_new_place() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        _, alice = await _resident(hub, "alice", name="Mildred")
        await hub.handle(alice, _act("move", place="tavern"))
        await hub.town.tick()
        assert hub.town._residents["alice"].place == "tavern"
        await hub.handle(alice, _act("say", text="A round for everyone!"))
        await hub.town.tick()
        say = [e for e in hub.town._events if e["type"] == "say"][-1]
        assert say["place"] == "tavern" and say["text"] == "A round for everyone!"

    asyncio.run(go())


def test_observation_locality_gossip_must_travel() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, alice = await _resident(hub, "alice", name="Mildred")
        b_conn, bob = await _resident(hub, "bob", name="Bosun")
        # Bob sails to the docks and mutters something there.
        await hub.handle(bob, _act("move", place="docks"))
        await hub.town.tick()
        await hub.handle(bob, _act("say", text="Mildred's bread is a rock."))
        await hub.town.tick()
        # Bob (at the docks) observes his own remark; Mildred (fountain) hears nothing.
        b_events = b_conn.last(models.TOWN_TICK)["events"]
        a_events = a_conn.last(models.TOWN_TICK)["events"]
        assert any("bread" in e.get("text", "") for e in b_events)
        assert not any("bread" in e.get("text", "") for e in a_events)
        # But the spectator broadcast (the fish tank view) carries it to everyone.
        assert any(
            "bread" in e.get("text", "")
            for e in a_conn.last(models.TOWN_STATE)["events"]
        )

    asyncio.run(go())


def test_say_is_capped_and_act_requires_join() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        _, alice = await _resident(hub, "alice")
        await hub.handle(alice, _act("say", text="x" * 1000))
        await hub.town.tick()
        say = [e for e in hub.town._events if e["type"] == "say"][-1]
        assert len(say["text"]) == SAY_MAX_CHARS
        # An authed session that never joined can't act.
        conn = FakeConn()
        stranger = hub.connect(conn)
        await hub.handle(stranger, {"type": models.AUTH, "token": "carol"})
        await hub.handle(stranger, _act("stay"))
        assert conn.last(models.ERROR)["code"] == "no_resident"

    asyncio.run(go())


def test_disconnect_sleeps_rejoin_wakes() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        conn, alice = await _resident(hub, "alice", name="Mildred")
        await hub.disconnect(alice)
        resident = hub.town._residents["alice"]
        assert resident.asleep  # the tank keeps the fish, dozing in place
        conn.messages.clear()
        await hub.town.tick()
        assert conn.last(models.TOWN_TICK) is None  # sleepers aren't prompted
        # Rejoining (same account, new connection) wakes the same resident.
        conn2, _ = await _resident(hub, "alice", name="Mildred")
        assert not hub.town._residents["alice"].asleep
        assert len(hub.town._residents) == 1
        assert conn2.last(models.TOWN_TICK) is not None

    asyncio.run(go())


def test_phases_cycle_with_ticks() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        assert hub.town.phase() == "morning"
        for _ in range(6):
            await hub.town.tick()
        assert hub.town.phase() == "afternoon"

    asyncio.run(go())


def test_work_only_at_the_job_site() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        _, alice = await _resident(hub, "alice", name="Mildred", avatar="🐙")
        fish = hub.town._residents["alice"]
        assert fish.job == "Fisherman"
        # Working at the fountain does nothing but an apologetic emote.
        await hub.handle(alice, _act("work"))
        await hub.town.tick()
        assert fish.wealth == 15.0
        assert any("can't work here" in e["text"] for e in hub.town._last_tick_events())
        # At the docks the same action pays and lands a fish.
        await hub.handle(alice, _act("move", place="docks"))
        await hub.town.tick()
        await hub.handle(alice, _act("work"))
        await hub.town.tick()
        assert fish.wealth == 25.0
        assert fish.inventory["fish"] == 1

    asyncio.run(go())


def test_buy_house_claims_a_lot_and_home_is_where_you_sleep() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        conn, alice = await _resident(hub, "alice", name="Mildred")
        fish = hub.town._residents["alice"]
        fish.wealth = 50.0
        # Buying requires standing in the residential lane.
        await hub.handle(alice, _act("buy_house"))
        await hub.town.tick()
        assert fish.house_id is None
        await hub.handle(alice, _act("move", place="residential_zone"))
        await hub.town.tick()
        await hub.handle(alice, _act("buy_house"))
        await hub.town.tick()
        assert fish.house_id == "lot1"
        assert fish.wealth == 20.0
        # The deed shows up in the spectator snapshot for the map.
        houses = conn.last(models.TOWN_STATE)["houses"]
        assert houses[0] == {
            "id": "lot1",
            "x": 8.0,
            "z": 28.0,
            "owner": "Mildred",
            "owner_id": "alice",
        }
        # Resting at home beats a park bench (+50 vs +20).
        fish.energy = 30.0
        await hub.handle(alice, _act("rest"))
        await hub.town.tick()
        assert fish.energy == 78.0  # 30 - 2 decay + 50 in your own bed
        # A disconnected homeowner sleeps *at home*, and leaving frees the lot.
        await hub.disconnect(alice)
        assert fish.asleep and (fish.x, fish.z) == (8.0, 28.0)

    asyncio.run(go())


def test_eat_consumes_bread_for_energy() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        _, alice = await _resident(hub, "alice", name="Mildred")
        fish = hub.town._residents["alice"]
        fish.energy, fish.inventory["bread"] = 50.0, 1
        await hub.handle(alice, _act("eat"))
        await hub.town.tick()
        assert fish.energy == 73.0  # 50 - 2 decay + 25 from the loaf
        assert fish.inventory["bread"] == 0
        # No food, no snack — just a hungry rumble.
        await hub.handle(alice, _act("eat"))
        await hub.town.tick()
        assert fish.energy == 71.0

    asyncio.run(go())


# ---- the resident's mind ------------------------------------------------------


def _you(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Mildred",
        "place": "fountain",
        "energy": 80.0,
        "strength": 10.0,
        "wealth": 15.0,
        "house_owned": False,
        "house_id": None,
        "job": "Clerk",
        "job_site": "workplace",
        "inventory": {"bread": 0, "fish": 0, "books": 0},
    }
    base.update(kw)
    return base


def _tick_msg(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tick": 3,
        "phase": "morning",
        "places": list(PLACES),
        "you": _you(),
        "occupants": [{"name": "Bosun", "avatar": "🦜"}],
        "events": [],
    }
    base.update(kw)
    return base


def test_routine_actions_are_always_valid() -> None:
    async def go() -> None:
        policy = TownPolicy(rng=random.Random(7))
        for i in range(40):
            action = await policy.decide(_tick_msg(tick=i), agent_mode=False)
            assert action["action"] in ACTIONS
            if action["action"] == "move":
                assert action["place"] in PLACES
            if action["action"] in ("say", "emote"):
                assert action["text"]

    asyncio.run(go())


def test_routine_heads_home_at_night() -> None:
    async def go() -> None:
        policy = TownPolicy(rng=random.Random(7))
        # Out on the town at night → walk home to the residential lane.
        action = await policy.decide(_tick_msg(phase="night"), agent_mode=False)
        assert action == {"action": "move", "place": "residential_zone"}
        # Home and tired → sleep; home and fully rested → doze contentedly.
        tired = _tick_msg(
            phase="night", you=_you(place="residential_zone", energy=50.0)
        )
        assert (await policy.decide(tired, agent_mode=False))["action"] == "rest"
        rested = _tick_msg(
            phase="night", you=_you(place="residential_zone", energy=100.0)
        )
        action = await policy.decide(rested, agent_mode=False)
        assert action["action"] == "emote" and "dozes" in action["text"]

    asyncio.run(go())


def test_routine_needs_drive_the_day() -> None:
    async def go() -> None:
        policy = TownPolicy(rng=random.Random(7))
        # Starving with bread in the pantry → eat it.
        hungry = _tick_msg(you=_you(energy=10.0, inventory={"bread": 2}))
        assert (await policy.decide(hungry, agent_mode=False))["action"] == "eat"
        # Broke → commute to *this job's* site, then work there.
        broke = _tick_msg(you=_you(wealth=2.0, job="Fisherman", job_site="docks"))
        assert await policy.decide(broke, agent_mode=False) == {
            "action": "move",
            "place": "docks",
        }
        at_work = _tick_msg(
            you=_you(wealth=2.0, place="docks", job="Fisherman", job_site="docks")
        )
        assert (await policy.decide(at_work, agent_mode=False))["action"] == "work"
        # Flush and homeless → head to the lane and buy a cottage.
        rich = _tick_msg(you=_you(wealth=45.0, place="residential_zone"))
        assert (await policy.decide(rich, agent_mode=False))["action"] == "buy_house"

    asyncio.run(go())


class _Call:
    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.name = name
        self.arguments = arguments


class _Result:
    def __init__(self, calls: list[_Call]) -> None:
        self.tool_calls = calls


def test_agent_mode_commits_via_tool_and_whisper_is_spent_once() -> None:
    async def go() -> None:
        prompts: list[str] = []

        async def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
            prompts.append(messages[-1]["content"])
            return _Result([_Call("town.act", {"action": "move", "place": "docks"})])

        policy = TownPolicy(chat_fn=chat, rng=random.Random(7))
        policy.whisper("go check on the docks")
        action = await policy.decide(_tick_msg(), agent_mode=True)
        assert action == {"action": "move", "place": "docks"}
        assert "go check on the docks" in prompts[0]  # the glass was tapped
        await policy.decide(_tick_msg(), agent_mode=True)
        assert "go check on the docks" not in prompts[1]  # a nudge is spent on use

    asyncio.run(go())


def test_agent_failure_falls_back_to_routine() -> None:
    async def go() -> None:
        async def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
            raise RuntimeError("provider down")

        policy = TownPolicy(chat_fn=chat, rng=random.Random(7))
        action = await policy.decide(_tick_msg(), agent_mode=True)
        assert action["action"] in ("stay", "move", "say", "emote")

    asyncio.run(go())


def test_agent_illegal_tool_args_fall_back() -> None:
    async def go() -> None:
        async def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
            return _Result([_Call("town.act", {"action": "move", "place": "narnia"})])

        policy = TownPolicy(chat_fn=chat, rng=random.Random(7))
        action = await policy.decide(_tick_msg(), agent_mode=True)
        # Invalid destination → routine keeps the fish swimming somewhere real.
        if action["action"] == "move":
            assert action["place"] in PLACES

    asyncio.run(go())
