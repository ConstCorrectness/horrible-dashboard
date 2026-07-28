"""The authoritative match server.

The server owns every player's position. Clients predict locally so movement
feels instant, but what the server simulates is what happened; a client that
disagrees is corrected. This is the standard prediction/reconciliation shape, and
the three pieces that make it work are:

**Every input carries a sequence number.** The client keeps each command it sent
until the server acknowledges it. Snapshots carry `ack` — the last command the
server consumed *from that client* — so the client knows exactly which of its own
predictions are still unconfirmed and can replay just those.

**The server advances a player only on that player's own commands**, each with
the `dt` the client measured, rather than ticking everyone by wall-clock. A
client whose frames are 8 ms and one whose frames are 33 ms then travel the same
distance per second, and — much more importantly — the server integrates exactly
the same sequence of steps the client predicted with, so a correct prediction
reconciles to zero error instead of to a small permanent jitter.

**Client-supplied `dt` is spent from a replenishing budget.** Trusting the client
for `dt` is trusting it for speed, so each player earns simulated time at real
time (plus a jitter allowance) into a small reservoir. Bursts after a stall are
absorbed; a client that simply claims time faster than it passes runs the
reservoir dry and is throttled to real time. It is a cap on the exploit, not a
lie detector — this is a game you host for friends, not a public ladder.

Movement and presence only: shooting needs hit registration and lag compensation,
which is its own problem and its own slice. See docs/modules/hassault.mdx.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.modules.hassault import assets, physics
from backend.modules.hassault.cgz import CgzError
from backend.modules.hassault.physics import MoveInput, PlayerState, World

if TYPE_CHECKING:
    from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

CHANNEL = "hassault"

# Snapshots per second. 20 Hz with ~100 ms of client-side interpolation delay is
# the classic Source-engine setting and holds up: remote players are rendered
# from two snapshots that have both already arrived, so ordinary jitter never
# shows. Raising this costs bandwidth linearly and buys very little.
SNAPSHOT_HZ = 20
TICK_INTERVAL = 1.0 / SNAPSHOT_HZ

# Simulated seconds a player may bank against real time. Roughly four frames:
# enough to absorb a stutter or a burst of coalesced commands, far too little to
# be worth exploiting.
BUDGET_CEILING = 0.25
# Jitter allowance on the earn rate. A client's clock and ours disagree slightly
# and its `dt` measurements are noisy; without a little headroom an honest client
# would be throttled by rounding.
BUDGET_EARN_RATE = 1.1

# Commands held for a player who is behind. Beyond this the oldest are dropped:
# an unbounded queue turns a lagging client into unbounded memory, and stale
# movement commands are worthless anyway by the time they would be simulated.
MAX_QUEUED_COMMANDS = 64

# No commands for this long and a player is shown as stale rather than silently
# standing still — the distinction matters when you are wondering whether to
# shoot at someone.
STALE_AFTER = 2.0

MAX_PLAYERS = 16
MAX_NAME_LEN = 24

# How long an empty room is kept before it is retired. A room opened for a friend
# who has not clicked the invite yet is empty and must survive; a room everyone
# has left is ~590 KB of map planes and should not.
EMPTY_GRACE = 60.0


@dataclass(slots=True)
class Command:
    """One client input frame."""

    seq: int
    forward: float
    strafe: float
    jump: bool
    yaw: float
    pitch: float
    dt: float


@dataclass(slots=True)
class MatchPlayer:
    id: str
    name: str
    team: int
    state: PlayerState
    conn: Any = None
    queue: deque[Command] = field(default_factory=deque)
    # Last command sequence the simulation has actually consumed. Sent back as
    # `ack` so the client knows what to replay.
    ack: int = 0
    # Highest sequence ever seen, used to drop duplicates and reorders. Distinct
    # from `ack`, which only moves when a command is simulated.
    high_seq: int = 0
    budget: float = 0.0
    last_command_at: float = field(default_factory=time.monotonic)
    joined_at: float = field(default_factory=time.monotonic)
    rtt_ms: float = 0.0

    def snapshot(self, now: float) -> dict[str, Any]:
        """The wire form. Rounded hard — a millimetre of a cube is not a thing
        anyone can see, and the digits are most of the packet."""
        return {
            "id": self.id,
            "name": self.name,
            "team": self.team,
            "x": round(self.state.x, 3),
            "y": round(self.state.y, 3),
            "z": round(self.state.z, 3),
            "yaw": round(self.state.yaw, 3),
            "pitch": round(self.state.pitch, 3),
            "ground": self.state.on_ground,
            "stale": (now - self.last_command_at) > STALE_AFTER,
            "rtt": round(self.rtt_ms),
        }


class MatchRoom:
    """One match on one map. Owns a tick task for as long as anyone is in it."""

    def __init__(self, room_id: str, map_name: str, world: World, spawns: list) -> None:
        """Takes a world and its spawns rather than a parsed map, so a test can
        build a room without AssaultCube content — which this repo cannot ship."""
        self.id = room_id
        self.map_name = map_name
        self.world = world
        self.spawns = spawns
        self.players: dict[str, MatchPlayer] = {}
        self.tick = 0
        self.created_at = time.time()
        # When the room last had someone in it. A room created for an invite is
        # empty until the invitee arrives, so "empty" alone cannot mean "retire".
        self.empty_since: float | None = time.monotonic()

    # -- membership ---------------------------------------------------------

    def _balanced_team(self) -> int:
        cla = sum(1 for p in self.players.values() if p.team == 0)
        rvsf = len(self.players) - cla
        return 0 if cla <= rvsf else 1

    def _spawn_state(self, team: int) -> PlayerState:
        """A spawn for `team`, falling back to any spawn, then to the middle.

        Not every map has spawns for both teams (and a few community maps have
        none at all), so each fallback is a real case rather than paranoia.
        """
        options = [e for e in self.spawns if e.attr2 == team] or self.spawns
        if not options:
            mid = self.world.ssize / 2
            return PlayerState(x=mid, y=mid, z=self.world.floor_at(int(mid), int(mid)))
        return physics.spawn_at(self.world, random.choice(options))

    def add(self, name: str, conn: Any) -> MatchPlayer:
        team = self._balanced_team()
        player = MatchPlayer(
            id=uuid.uuid4().hex[:12],
            name=name,
            team=team,
            state=self._spawn_state(team),
            conn=conn,
        )
        self.players[player.id] = player
        self.empty_since = None
        return player

    def remove(self, player_id: str) -> MatchPlayer | None:
        gone = self.players.pop(player_id, None)
        if not self.players:
            self.empty_since = time.monotonic()
        return gone

    def respawn(self, player: MatchPlayer) -> None:
        player.state = self._spawn_state(player.team)
        # Drop queued commands: they were predicted against the old position, and
        # simulating them after a teleport walks the player away from the spawn.
        player.queue.clear()

    # -- simulation ---------------------------------------------------------

    def enqueue(self, player: MatchPlayer, command: Command) -> None:
        # Duplicates and reorders are normal on a lossy link; the sequence number
        # is what makes them cheap to ignore.
        if command.seq <= player.high_seq:
            return
        player.high_seq = command.seq
        player.last_command_at = time.monotonic()
        player.queue.append(command)
        while len(player.queue) > MAX_QUEUED_COMMANDS:
            player.queue.popleft()

    def simulate(self, elapsed: float) -> None:
        """Drain each player's queue, spending from their time budget."""
        for player in self.players.values():
            player.budget = min(
                BUDGET_CEILING, player.budget + elapsed * BUDGET_EARN_RATE
            )
            while player.queue:
                command = player.queue[0]
                dt = min(max(command.dt, 0.0), physics.MAX_STEP_DT)
                if dt > player.budget:
                    # Out of credit: leave the command queued and let it run next
                    # tick. Throttling rather than dropping keeps an honest but
                    # stuttering client's movement continuous.
                    break
                player.queue.popleft()
                player.budget -= dt
                # View angles are cosmetic on the server but they steer movement,
                # so they are applied before the step, not after.
                player.state.yaw = command.yaw
                player.state.pitch = command.pitch
                physics.step(
                    self.world,
                    player.state,
                    MoveInput(
                        forward=command.forward,
                        strafe=command.strafe,
                        jump=command.jump,
                        yaw=command.yaw,
                        pitch=command.pitch,
                        dt=dt,
                        seq=command.seq,
                    ),
                    dt,
                )
                player.ack = command.seq

    def snapshot_for(self, player: MatchPlayer, now: float, rows: list[dict]) -> dict:
        return {
            "channel": CHANNEL,
            "event": "snapshot",
            "data": {
                "room": self.id,
                "tick": self.tick,
                # Server clock in ms, so a client can measure one-way drift and
                # order snapshots without trusting arrival order.
                "t": round(now * 1000),
                "ack": player.ack,
                "players": rows,
            },
        }

    def state_payload(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "room": self.id,
            "map": self.map_name,
            "tick": self.tick,
            "snapshotHz": SNAPSHOT_HZ,
            "players": [p.snapshot(now) for p in self.players.values()],
        }


class MatchServer:
    """Process-global registry of rooms, and the tick loop that drives them."""

    def __init__(self) -> None:
        self.rooms: dict[str, MatchRoom] = {}
        # Which room each connection's player is in, so a socket closing can be
        # cleaned up without searching every room.
        self.membership: dict[int, tuple[str, str]] = {}
        self._task: asyncio.Task[None] | None = None

    # -- rooms --------------------------------------------------------------

    def create(self, map_name: str, room_id: str | None = None) -> MatchRoom:
        cgz = assets.load_map(map_name)
        if cgz is None:
            raise LookupError(f"no map named {map_name!r}")
        rid = room_id or uuid.uuid4().hex[:8]
        room = MatchRoom(rid, map_name, World.from_map(cgz), cgz.spawns())
        self.rooms[rid] = room
        # Start ticking even though the room is empty: the tick loop is also what
        # retires it, so a room opened for an invite nobody accepts would
        # otherwise hold its map planes until some unrelated match began.
        try:
            self.ensure_running()
        except RuntimeError:
            # No running loop (a synchronous caller in a test). The next join
            # starts the loop anyway.
            pass
        return room

    def get(self, room_id: str) -> MatchRoom | None:
        return self.rooms.get(room_id)

    def find_or_create(self, map_name: str) -> MatchRoom:
        """The first room on `map_name` with space, else a new one.

        "Join a map" is what a player actually wants; explicit room ids exist for
        the friends-list invite path, which hands one over.
        """
        for room in self.rooms.values():
            if room.map_name == map_name and len(room.players) < MAX_PLAYERS:
                return room
        return self.create(map_name)

    def listing(self) -> list[dict[str, Any]]:
        return [
            {
                "id": room.id,
                "map": room.map_name,
                "players": len(room.players),
                "maxPlayers": MAX_PLAYERS,
                "createdAt": room.created_at,
            }
            for room in self.rooms.values()
        ]

    # -- lifecycle ----------------------------------------------------------

    def ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        """Fixed-rate loop: simulate, broadcast, retire empty rooms.

        Sleeps the remainder of the interval rather than a flat `TICK_INTERVAL`,
        so the tick rate does not silently sag under load.
        """
        last = time.monotonic()
        try:
            while self.rooms:
                started = time.monotonic()
                elapsed = started - last
                last = started
                for room in list(self.rooms.values()):
                    if not room.players:
                        if (started - (room.empty_since or started)) > EMPTY_GRACE:
                            self.rooms.pop(room.id, None)
                        continue
                    room.tick += 1
                    room.simulate(elapsed)
                    await self._broadcast(room)
                await asyncio.sleep(
                    max(0.0, TICK_INTERVAL - (time.monotonic() - started))
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("hassault match loop failed")

    async def _broadcast(self, room: MatchRoom) -> None:
        now = time.time()
        mono = time.monotonic()
        # One shared list of rows, but a per-player envelope: `ack` is the only
        # field that differs, and it is the field prediction depends on.
        rows = [p.snapshot(mono) for p in room.players.values()]
        for player in list(room.players.values()):
            conn = player.conn
            if conn is None:
                continue
            try:
                await conn.send_json(room.snapshot_for(player, now, rows))
            except Exception:
                # A dead socket is the /ws loop's problem; dropping the player
                # here would race its own disconnect handling.
                pass

    async def broadcast_event(
        self, room: MatchRoom, event: str, data: dict[str, Any], exclude: str = ""
    ) -> None:
        message = {"channel": CHANNEL, "event": event, "data": data}
        for player in list(room.players.values()):
            if player.id == exclude or player.conn is None:
                continue
            try:
                await player.conn.send_json(message)
            except Exception:
                pass

    # -- membership ---------------------------------------------------------

    async def join(
        self, conn: WsConnection, map_name: str, name: str, room_id: str | None = None
    ) -> tuple[MatchRoom, MatchPlayer]:
        await self.leave(conn)
        if room_id:
            room = self.get(room_id)
            if room is None:
                raise LookupError(f"no match {room_id!r}")
        else:
            room = self.find_or_create(map_name)
        if len(room.players) >= MAX_PLAYERS:
            raise ValueError("that match is full")
        player = room.add(name[:MAX_NAME_LEN] or "player", conn)
        self.membership[id(conn)] = (room.id, player.id)
        self.ensure_running()
        await self.broadcast_event(
            room,
            "joined",
            {"room": room.id, "player": player.snapshot(time.monotonic())},
            player.id,
        )
        return room, player

    async def leave(self, conn: WsConnection) -> None:
        entry = self.membership.pop(id(conn), None)
        if entry is None:
            return
        room_id, player_id = entry
        room = self.rooms.get(room_id)
        if room is None:
            return
        room.remove(player_id)
        await self.broadcast_event(
            room, "left", {"room": room.id, "playerId": player_id}
        )

    def player_for(self, conn: WsConnection) -> tuple[MatchRoom, MatchPlayer] | None:
        entry = self.membership.get(id(conn))
        if entry is None:
            return None
        room = self.rooms.get(entry[0])
        if room is None:
            return None
        player = room.players.get(entry[1])
        return (room, player) if player else None

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.rooms.clear()
        self.membership.clear()


match_server = MatchServer()


def map_error(exc: Exception) -> str:
    """A message worth showing a player, for the map-loading failures."""
    if isinstance(exc, CgzError):
        return f"that map cannot be read: {exc}"
    return str(exc)
