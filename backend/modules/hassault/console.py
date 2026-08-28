"""Developer Console, CVar/ConCommand registry, and Python Macro engine for HorribleAssault.

Provides a Counter-Strike / Source-engine style developer console modernized into a
typed, hierarchical variable and macro execution system.

Namespaces:
- `net.*`: Network latency simulation, NetGraph HUD, packet loss/jitter, prediction controls
- `draw.*`: Visual debugging, hitboxes, trajectories, wireframe, FPS, FOV, crosshair
- `server.*`: Match hosting, map rotation, timescale slow-mo, sv_cheats, bot orchestration
- `player.*`: Noclip, godmode, weapon giving, health, coordinates, respawning
- `physics.*` / `hitbox.*`: World physics constants and live hitbox specification tuning
- `macro.*`: Script management, execution, and keybindings
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import math
import re
import shlex
import time
from typing import Any, Callable, Coroutine, Literal

from pydantic import BaseModel, Field

from backend.paths import data_dir
from backend.modules.hassault import assets, bots, hitbox, weapons
from backend.modules.hassault.match import MAX_PLAYERS, match_server

logger = logging.getLogger(__name__)

MACROS_FILE = data_dir() / "hassault_macros.json"

CVarType = Literal["boolean", "number", "string", "enum"]
CVarFlag = Literal["cheat", "server", "client", "replicated", "archived", "readonly"]


class CVarDefinition(BaseModel):
    name: str
    namespace: str
    type: CVarType
    default_value: Any
    current_value: Any
    min_value: float | None = None
    max_value: float | None = None
    enum_values: list[str] | None = None
    description: str = ""
    flags: list[CVarFlag] = Field(default_factory=list)
    python_attr: str = ""


class ConCommandParameter(BaseModel):
    name: str
    type: str
    default: Any = None
    description: str = ""
    required: bool = False
    enum_values: list[str] | None = None


class ConCommandDefinition(BaseModel):
    name: str
    namespace: str
    description: str = ""
    signature: str = ""
    parameters: list[ConCommandParameter] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    example: str = ""


class MacroDefinition(BaseModel):
    name: str
    description: str = ""
    code: str
    author: str = "local"
    builtin: bool = False
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ConsoleExecRequest(BaseModel):
    command: str
    room_id: str | None = None
    player_id: str | None = None
    client_context: dict[str, Any] = Field(default_factory=dict)


class ConsoleExecResponse(BaseModel):
    ok: bool
    command: str
    output: list[str] = Field(default_factory=list)
    error: str | None = None
    affected_cvars: dict[str, Any] = Field(default_factory=dict)
    result_data: Any = None


class ConsoleDefinitionsResponse(BaseModel):
    cvars: list[CVarDefinition]
    commands: list[ConCommandDefinition]
    macros: list[MacroDefinition]


# -----------------------------------------------------------------------------
# Default Built-in Macros
# -----------------------------------------------------------------------------

BUILTIN_MACROS: list[MacroDefinition] = [
    MacroDefinition(
        name="warmup",
        description="Warmup practice mode: god mode, infinite ammo, hitboxes, bullet tracers, 3 bots",
        code=(
            "server.cheats = True\n"
            "player.god = True\n"
            "player.infinite_ammo = True\n"
            "draw.hitboxes = True\n"
            "draw.trajectories = True\n"
            'server.bots.add(count=3, skill="normal")\n'
            'print("[macro] Warmup initialized: God mode ON, Infinite Ammo ON, 3 Bots spawned.")'
        ),
        builtin=True,
    ),
    MacroDefinition(
        name="bot_1v5",
        description="Challenge drill: 1 vs 5 Hard Bots on opposing team",
        code=(
            "server.cheats = True\n"
            "server.bots.kick_all()\n"
            'server.bots.add(count=5, skill="hard", team="RVSF")\n'
            'player.give("carbine")\n'
            'print("[macro] 1v5 Bot Challenge Drill started against 5 Hard RVSF bots!")'
        ),
        builtin=True,
    ),
    MacroDefinition(
        name="smoke_practice",
        description="Grenade / trajectory practice with visual tracers and free respawn",
        code=(
            "server.cheats = True\n"
            "player.god = True\n"
            "player.infinite_ammo = True\n"
            "draw.trajectories = True\n"
            "draw.hitboxes = True\n"
            'print("[macro] Trajectory & Grenade practice lab ready.")'
        ),
        builtin=True,
    ),
    MacroDefinition(
        name="net_stress_test",
        description="Simulate laggy network conditions: 120ms ping, 5% loss, NetGraph level 2",
        code=(
            "net.simulate_lag = 120\n"
            "net.simulate_loss = 0.05\n"
            "net.graph = 2\n"
            'print("[macro] Network Stress Test active (Lag: 120ms, Loss: 5%, NetGraph: 2)")'
        ),
        builtin=True,
    ),
    MacroDefinition(
        name="slowmo_duel",
        description="Slow motion Matrix-style firefight (0.35x timescale)",
        code=(
            "server.cheats = True\n"
            "server.timescale = 0.35\n"
            'print("[macro] Matrix Slow-Mo enabled (Timescale: 0.35x)")'
        ),
        builtin=True,
    ),
]


class ConsoleRegistry:
    """Central registry of Console Variables (CVars), Console Commands (ConCommands),
    and Macros for hAssault."""

    def __init__(self) -> None:
        self.cvars: dict[str, CVarDefinition] = {}
        self.commands: dict[str, ConCommandDefinition] = {}
        self.handlers: dict[
            str,
            Callable[
                [dict[str, Any], ConsoleExecutionContext], Coroutine[Any, Any, Any]
            ],
        ] = {}
        self.macros: dict[str, MacroDefinition] = {}
        self._init_defaults()
        self._load_macros()

    def _register_cvar(
        self,
        name: str,
        namespace: str,
        type_: CVarType,
        default: Any,
        description: str = "",
        min_val: float | None = None,
        max_val: float | None = None,
        enum_vals: list[str] | None = None,
        flags: list[CVarFlag] | None = None,
        python_attr: str = "",
    ) -> None:
        cvar = CVarDefinition(
            name=name,
            namespace=namespace,
            type=type_,
            default_value=default,
            current_value=default,
            min_value=min_val,
            max_value=max_val,
            enum_values=enum_vals,
            description=description,
            flags=flags or [],
            python_attr=python_attr or name,
        )
        self.cvars[name] = cvar

    def _register_command(
        self,
        name: str,
        namespace: str,
        handler: Callable[
            [dict[str, Any], ConsoleExecutionContext], Coroutine[Any, Any, Any]
        ],
        description: str = "",
        signature: str = "",
        params: list[ConCommandParameter] | None = None,
        flags: list[str] | None = None,
        example: str = "",
    ) -> None:
        cmd = ConCommandDefinition(
            name=name,
            namespace=namespace,
            description=description,
            signature=signature,
            parameters=params or [],
            flags=flags or [],
            example=example,
        )
        self.commands[name] = cmd
        self.handlers[name] = handler

    def _init_defaults(self) -> None:
        # --- net.* ---
        self._register_cvar(
            "net.graph",
            "net",
            "number",
            0,
            "Draw in-game network graph and performance stats (0: off, 1: fps/ping, 2: jitter/interp, 3: full breakdown)",
            min_val=0,
            max_val=3,
            flags=["client"],
        )
        self._register_cvar(
            "net.simulate_lag",
            "net",
            "number",
            0.0,
            "Inject artificial one-way latency in milliseconds to test prediction and reconciliation",
            min_val=0.0,
            max_val=1000.0,
            flags=["client", "cheat"],
        )
        self._register_cvar(
            "net.simulate_loss",
            "net",
            "number",
            0.0,
            "Inject artificial packet loss fraction (0.0 to 1.0)",
            min_val=0.0,
            max_val=1.0,
            flags=["client", "cheat"],
        )
        self._register_cvar(
            "net.simulate_jitter",
            "net",
            "number",
            0.0,
            "Inject artificial packet jitter in milliseconds",
            min_val=0.0,
            max_val=250.0,
            flags=["client", "cheat"],
        )
        self._register_cvar(
            "net.prediction",
            "net",
            "boolean",
            True,
            "Toggle client-side movement prediction",
            flags=["client"],
        )
        self._register_cvar(
            "net.reconciliation",
            "net",
            "boolean",
            True,
            "Toggle server snapshot position and velocity reconciliation",
            flags=["client"],
        )
        self._register_cvar(
            "net.interp_delay",
            "net",
            "number",
            50.0,
            "Snapshot interpolation buffer delay in milliseconds",
            min_val=0.0,
            max_val=250.0,
            flags=["client"],
        )

        # --- draw.* ---
        self._register_cvar(
            "draw.hitboxes",
            "draw",
            "boolean",
            False,
            "Render live authoritative hitboxes and headshot bands around player bodies",
            flags=["client"],
        )
        self._register_cvar(
            "draw.trajectories",
            "draw",
            "boolean",
            True,
            "Trace bullet raycast trajectories, wall hits, and player impact points",
            flags=["client"],
        )
        self._register_cvar(
            "draw.wireframe",
            "draw",
            "boolean",
            False,
            "Render world map geometry in wireframe mode",
            flags=["client"],
        )
        self._register_cvar(
            "draw.fps",
            "draw",
            "boolean",
            False,
            "Display current frames per second (FPS) and frame-time counter",
            flags=["client"],
        )
        self._register_cvar(
            "draw.noise_rings",
            "draw",
            "boolean",
            True,
            "Display spatial noise bearing indicators around crosshair",
            flags=["client"],
        )
        self._register_cvar(
            "draw.fov",
            "draw",
            "number",
            75.0,
            "Vertical field of view in degrees (60-110)",
            min_val=60.0,
            max_val=110.0,
            flags=["client", "archived"],
        )
        self._register_cvar(
            "draw.viewmodel",
            "draw",
            "boolean",
            True,
            "Render first-person animated weapon viewmodel",
            flags=["client"],
        )
        self._register_cvar(
            "draw.render_scale",
            "draw",
            "number",
            1.0,
            "Resolution render scale factor (0.5 - 1.0)",
            min_val=0.5,
            max_val=1.0,
            flags=["client"],
        )
        self._register_cvar(
            "draw.crosshair.style",
            "draw",
            "enum",
            "cross",
            "Crosshair geometry style",
            enum_vals=["cross", "crossDot", "dot", "circle"],
            flags=["client", "archived"],
        )
        self._register_cvar(
            "draw.crosshair.size",
            "draw",
            "number",
            3.0,
            "Crosshair arm length (1-12)",
            min_val=1.0,
            max_val=12.0,
            flags=["client", "archived"],
        )
        self._register_cvar(
            "draw.crosshair.gap",
            "draw",
            "number",
            4.0,
            "Crosshair inner gap (0-20)",
            min_val=0.0,
            max_val=20.0,
            flags=["client", "archived"],
        )
        self._register_cvar(
            "draw.crosshair.thickness",
            "draw",
            "number",
            0.6,
            "Crosshair stroke thickness (0.2-3.0)",
            min_val=0.2,
            max_val=3.0,
            flags=["client", "archived"],
        )
        self._register_cvar(
            "draw.crosshair.color",
            "draw",
            "enum",
            "white",
            "Crosshair color preset",
            enum_vals=["white", "green", "cyan", "amber", "magenta", "red"],
            flags=["client", "archived"],
        )

        # --- server.* ---
        self._register_cvar(
            "server.cheats",
            "server",
            "boolean",
            False,
            "Allow cheat-protected commands (godmode, noclip, give, timescale) in hosted match",
            flags=["server", "replicated"],
        )
        self._register_cvar(
            "server.timescale",
            "server",
            "number",
            1.0,
            "Simulation speed multiplier (0.05x matrix slow-mo to 5.0x fast-forward)",
            min_val=0.05,
            max_val=5.0,
            flags=["server", "cheat", "replicated"],
        )
        self._register_cvar(
            "server.max_players",
            "server",
            "number",
            16,
            "Maximum player slots allowed in match",
            min_val=1,
            max_val=MAX_PLAYERS,
            flags=["server"],
        )
        self._register_cvar(
            "server.bots.freeze",
            "server",
            "boolean",
            False,
            "Freeze all bot AI navigation, targeting, and decision loops",
            flags=["server", "cheat"],
        )

        # --- player.* ---
        self._register_cvar(
            "player.god",
            "player",
            "boolean",
            False,
            "Invulnerability / god mode (take no damage)",
            flags=["cheat", "server"],
        )
        self._register_cvar(
            "player.noclip",
            "player",
            "boolean",
            False,
            "Fly freely through walls and level geometry",
            flags=["cheat", "client"],
        )
        self._register_cvar(
            "player.infinite_ammo",
            "player",
            "boolean",
            False,
            "Infinite magazine and reserve ammo without reloading",
            flags=["cheat", "server"],
        )
        self._register_cvar(
            "player.speed_scale",
            "player",
            "number",
            1.0,
            "Movement speed multiplier (0.1 - 5.0)",
            min_val=0.1,
            max_val=5.0,
            flags=["cheat", "server"],
        )
        self._register_cvar(
            "player.sensitivity",
            "player",
            "number",
            1.0,
            "Mouse look sensitivity multiplier",
            min_val=0.1,
            max_val=10.0,
            flags=["client", "archived"],
        )

        # --- physics.* ---
        self._register_cvar(
            "physics.gravity",
            "physics",
            "number",
            32.0,
            "World gravity constant in cubes/sec^2",
            min_val=0.0,
            max_val=120.0,
            flags=["server", "cheat"],
        )
        self._register_cvar(
            "physics.jump_boost",
            "physics",
            "number",
            1.25,
            "Chained-jump landing speed multiplier",
            min_val=1.0,
            max_val=3.0,
            flags=["server", "cheat"],
        )
        self._register_cvar(
            "physics.step_height",
            "physics",
            "number",
            1.6,
            "Maximum obstacle step-up height in cubes",
            min_val=0.0,
            max_val=5.0,
            flags=["server", "cheat"],
        )

        # --- ConCommands ---
        self._init_commands()

    def _init_commands(self) -> None:
        # server.start
        self._register_command(
            "server.start",
            "server",
            self._cmd_server_start,
            description="Open and host a match on a map, optionally spawning bots",
            signature="server.start(map: str, bots: int = 0, skill: str = 'normal')",
            params=[
                ConCommandParameter(
                    name="map",
                    type="string",
                    required=True,
                    description="Map name (e.g. hd_atrium, hd_pit)",
                ),
                ConCommandParameter(
                    name="bots",
                    type="integer",
                    default=0,
                    description="Number of bots to field",
                ),
                ConCommandParameter(
                    name="skill",
                    type="string",
                    default="normal",
                    enum_values=list(bots.SKILLS),
                    description="Bot difficulty",
                ),
            ],
            example='server.start("hd_atrium", bots=3, skill="hard")',
        )

        # server.stop
        self._register_command(
            "server.stop",
            "server",
            self._cmd_server_stop,
            description="Close the match running on this node",
            signature="server.stop(room: str = '')",
            example="server.stop()",
        )

        # server.tick
        self._register_command(
            "server.tick",
            "server",
            self._cmd_server_tick,
            description="Report where the match tick's 50 ms budget is going",
            signature="server.tick(room: str = '')",
            example="server.tick()",
        )

        # server.map
        self._register_command(
            "server.map",
            "server",
            self._cmd_server_map,
            description="Change the map of the current match",
            signature="server.map(map_name: str)",
            params=[
                ConCommandParameter(
                    name="map_name",
                    type="string",
                    required=True,
                    description="Map name",
                )
            ],
            example='server.map("hd_crossing")',
        )

        # server.restart
        self._register_command(
            "server.restart",
            "server",
            self._cmd_server_restart,
            description="Reset scores and respawn all players in match",
            signature="server.restart()",
            example="server.restart()",
        )

        # server.bots.add
        self._register_command(
            "server.bots.add",
            "server",
            self._cmd_bots_add,
            description="Add bot players to the match",
            signature="server.bots.add(count: int = 1, skill: str = 'normal', team: str = None)",
            params=[
                ConCommandParameter(
                    name="count", type="integer", default=1, description="Bot count"
                ),
                ConCommandParameter(
                    name="skill",
                    type="string",
                    default="normal",
                    enum_values=list(bots.SKILLS),
                ),
                ConCommandParameter(
                    name="team",
                    type="string",
                    default=None,
                    enum_values=["CLA", "RVSF"],
                ),
            ],
            example='server.bots.add(count=3, skill="hard", team="RVSF")',
        )

        # server.bots.remove
        self._register_command(
            "server.bots.remove",
            "server",
            self._cmd_bots_remove,
            description="Remove bots from the match",
            signature="server.bots.remove(name: str = None, count: int = 1)",
            params=[
                ConCommandParameter(
                    name="name", type="string", default=None, description="Bot name"
                ),
                ConCommandParameter(
                    name="count", type="integer", default=1, description="Bot count"
                ),
            ],
            example="server.bots.remove(count=2)",
        )

        # server.bots.kick_all
        self._register_command(
            "server.bots.kick_all",
            "server",
            self._cmd_bots_kick_all,
            description="Kick all bots out of the current match",
            signature="server.bots.kick_all()",
            example="server.bots.kick_all()",
        )

        # player.give
        self._register_command(
            "player.give",
            "player",
            self._cmd_player_give,
            description="Equip a weapon by name or ID (knife, pistol, carbine, shotgun, sniper, subgun)",
            signature="player.give(weapon: str)",
            params=[
                ConCommandParameter(
                    name="weapon",
                    type="string",
                    required=True,
                    enum_values=[
                        "knife",
                        "pistol",
                        "carbine",
                        "shotgun",
                        "sniper",
                        "subgun",
                    ],
                )
            ],
            flags=["cheat"],
            example='player.give("sniper")',
        )

        # player.teleport
        self._register_command(
            "player.teleport",
            "player",
            self._cmd_player_teleport,
            description="Teleport player to target map coordinates (x, y, z)",
            signature="player.teleport(x: float, y: float, z: float)",
            params=[
                ConCommandParameter(name="x", type="number", required=True),
                ConCommandParameter(name="y", type="number", required=True),
                ConCommandParameter(name="z", type="number", required=True),
            ],
            flags=["cheat"],
            example="player.teleport(16, 24, 5)",
        )

        # player.respawn
        self._register_command(
            "player.respawn",
            "player",
            self._cmd_player_respawn,
            description="Force immediate player respawn",
            signature="player.respawn()",
            example="player.respawn()",
        )

        # player.get_pos
        self._register_command(
            "player.get_pos",
            "player",
            self._cmd_player_get_pos,
            description="Print player's current world coordinates and facing angles",
            signature="player.get_pos()",
            example="player.get_pos()",
        )

        # hitbox.get & hitbox.tune
        self._register_command(
            "hitbox.get",
            "hitbox",
            self._cmd_hitbox_get,
            description="Inspect the active player hitbox specification and content hash",
            signature="hitbox.get()",
            example="hitbox.get()",
        )
        self._register_command(
            "hitbox.tune",
            "hitbox",
            self._cmd_hitbox_tune,
            description="Override hitbox dimensions (radius, eye_height, head_band)",
            signature="hitbox.tune(radius: float = None, head_band: float = None)",
            params=[
                ConCommandParameter(name="radius", type="number", default=None),
                ConCommandParameter(name="head_band", type="number", default=None),
            ],
            flags=["cheat"],
            example="hitbox.tune(radius=1.2, head_band=0.8)",
        )
        self._register_command(
            "hitbox.reset",
            "hitbox",
            self._cmd_hitbox_reset,
            description="Reset hitbox override back to canonical spec",
            signature="hitbox.reset()",
            example="hitbox.reset()",
        )

        # macro.run & macro.list
        self._register_command(
            "macro.run",
            "macro",
            self._cmd_macro_run,
            description="Execute a stored Python macro by name",
            signature="macro.run(name: str)",
            params=[
                ConCommandParameter(
                    name="name",
                    type="string",
                    required=True,
                    description="Macro name",
                )
            ],
            example='macro.run("warmup")',
        )
        self._register_command(
            "macro.list",
            "macro",
            self._cmd_macro_list,
            description="List all available macros and scripts",
            signature="macro.list()",
            example="macro.list()",
        )

        # help & find
        self._register_command(
            "help",
            "system",
            self._cmd_help,
            description="List all CVars and commands or search with query",
            signature="help(query: str = '')",
            params=[ConCommandParameter(name="query", type="string", default="")],
            example='help("bot")',
        )

    # -------------------------------------------------------------------------
    # Macro Persistence
    # -------------------------------------------------------------------------

    def _load_macros(self) -> None:
        self.macros = {m.name: m for m in BUILTIN_MACROS}
        if MACROS_FILE.exists():
            try:
                data = json.loads(MACROS_FILE.read_text(encoding="utf-8"))
                for row in data:
                    macro = MacroDefinition(**row)
                    self.macros[macro.name] = macro
            except Exception:
                logger.exception("Failed to load user macros from %s", MACROS_FILE)

    def save_macro(self, name: str, code: str, desc: str = "") -> MacroDefinition:
        clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_")
        macro = MacroDefinition(
            name=clean_name,
            description=desc,
            code=code,
            author="local",
            builtin=False,
            updated_at=time.time(),
        )
        self.macros[clean_name] = macro
        self._persist_user_macros()
        return macro

    def delete_macro(self, name: str) -> bool:
        if name in self.macros and not self.macros[name].builtin:
            del self.macros[name]
            self._persist_user_macros()
            return True
        return False

    def _persist_user_macros(self) -> None:
        try:
            user_macros = [
                m.model_dump() for m in self.macros.values() if not m.builtin
            ]
            MACROS_FILE.write_text(json.dumps(user_macros, indent=2), encoding="utf-8")
        except Exception:
            logger.exception("Failed to persist user macros")

    # -------------------------------------------------------------------------
    # Command Implementations
    # -------------------------------------------------------------------------

    async def _cmd_server_start(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        map_name = str(args.get("map") or "hd_atrium").strip()
        bots_count = int(args.get("bots") or 0)
        skill = str(args.get("skill") or "normal").lower()
        room = match_server.create(map_name)
        added_names: list[str] = []
        if bots_count > 0:
            added = bots.add_bots(room, min(bots_count, MAX_PLAYERS), skill)
            added_names = [b.name for b in added]
        ctx.print(
            f"[server] Hosted match room '{room.id}' on map '{map_name}' "
            f"with {len(added_names)} bots ({skill})."
        )
        return {
            "room": room.id,
            "map": room.map_name,
            "bots": added_names,
            "skill": skill,
        }

    async def _cmd_server_stop(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        room = ctx.resolve_room(args.get("room"))
        if room is None:
            ctx.print("[server] No active match to stop.")
            return {"error": "no match"}
        room_id = room.id
        match_server.retire(room_id)
        ctx.print(f"[server] Match room '{room_id}' closed.")
        return {"stopped": room_id}

    async def _cmd_server_tick(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        """Where the tick's budget goes.

        A read, not a cvar: `TickStats` is a measurement and cvars are things you
        set. The loop sleeps the *remainder* of `TICK_INTERVAL`, so a room that
        is running out of budget quietly slows down instead of reporting it —
        this is the only place that headroom is visible.
        """
        room = ctx.resolve_room(args.get("room"))
        if room is None:
            ctx.print("[server] No active match.")
            return {"error": "no match"}
        report = room.stats.report()
        budget = report["budgetMs"]
        ctx.print(f"[server] Room '{room.id}' — {budget} ms per tick to spend.")
        for label, key in (("simulate", "simulateMs"), ("broadcast", "broadcastMs")):
            stat = report[key]
            if not stat["samples"]:
                # Never "0.0 ms": a room that has not ticked has not been
                # measured, and saying it is free would be a lie the numbers
                # cannot distinguish from a fast one.
                ctx.print(f"[server]   {label}: not measured yet")
                continue
            share = stat["mean"] / budget * 100
            ctx.print(
                f"[server]   {label}: {stat['mean']} ms mean "
                f"({share:.0f}% of budget), {stat['max']} ms peak "
                f"over {stat['samples']} ticks"
            )
        sent = report["tickBytes"]
        if sent["samples"]:
            ctx.print(
                f"[server]   wire: {sent['mean'] / 1024:.1f} KiB per tick "
                f"across all recipients ({sent['mean'] * 20 / 1024:.0f} KiB/s)"
            )
        else:
            # Only the pre-serialised path weighs itself; the dict path would
            # have to serialise twice to answer.
            ctx.print("[server]   wire: not measured (plain send path)")
        return report

    async def _cmd_server_map(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        map_name = str(args.get("map_name") or args.get("map") or "").strip()
        if not map_name:
            raise ValueError("map name required")
        world = assets.load_map(map_name)
        if world is None:
            raise ValueError(f"no map named {map_name!r}")
        room = ctx.resolve_room()
        if room is None:
            room = match_server.create(map_name)
            ctx.print(f"[server] Started new match on '{map_name}' (room {room.id}).")
            return {"room": room.id, "map": map_name}
        room.map_name = map_name
        room.world = world
        room.spawns = assets.player_spawns(map_name)
        room.scores = [0, 0]
        for p in room.players.values():
            room.respawn(p)
        ctx.print(f"[server] Map changed to '{map_name}' for room '{room.id}'.")
        return {"map": map_name, "room": room.id}

    async def _cmd_server_restart(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        room = ctx.resolve_room()
        if room is None:
            ctx.print("[server] No match running.")
            return {"error": "no match"}
        room.scores = [0, 0]
        for p in room.players.values():
            room.kills = 0
            room.deaths = 0
            room.respawn(p)
        ctx.print(
            f"[server] Match scores reset and players respawned (room {room.id})."
        )
        return {"restarted": room.id}

    async def _cmd_bots_add(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        room = ctx.resolve_room()
        if room is None:
            ctx.print("[server] No match running. Start one first with server.start()")
            return {"error": "no match"}
        count = int(args.get("count") or 1)
        skill = str(args.get("skill") or "normal").lower()
        team_str = args.get("team")
        team = (
            {"cla": 0, "rvsf": 1}.get(str(team_str).strip().lower())
            if team_str
            else None
        )
        added = bots.add_bots(room, count, skill, team)
        names = [b.name for b in added]
        ctx.print(f"[server] Spawned {len(names)} bots ({skill}): {', '.join(names)}")
        return {"added": names, "skill": skill, "total": len(room.players)}

    async def _cmd_bots_remove(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        room = ctx.resolve_room()
        if room is None:
            ctx.print("[server] No match running.")
            return {"error": "no match"}
        name = str(args.get("name") or "").strip().lower()
        if name:
            match = next(
                (
                    p
                    for p in room.players.values()
                    if p.is_bot and name in p.name.lower()
                ),
                None,
            )
            if match:
                room.remove(match.id)
                ctx.print(f"[server] Removed bot {match.name}.")
                return {"removed": [match.name]}
            ctx.print(f"[server] No bot matching {name!r} found.")
            return {"removed": []}
        count = int(args.get("count") or 1)
        removed = room.remove_bots(count)
        ctx.print(f"[server] Removed {removed} bots.")
        return {"removed_count": removed}

    async def _cmd_bots_kick_all(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        room = ctx.resolve_room()
        if room is None:
            ctx.print("[server] No match running.")
            return {"error": "no match"}
        removed = room.remove_bots(None)
        ctx.print(f"[server] Kicked all bots ({removed} removed).")
        return {"removed_count": removed}

    async def _cmd_player_give(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        weapon_query = str(args.get("weapon") or "carbine").strip().lower()
        weapon_idx = weapons.WEAPON_NAMES.get(weapon_query)
        if weapon_idx is None:
            try:
                weapon_idx = int(weapon_query)
            except ValueError:
                weapon_idx = None
        if weapon_idx is None or weapon_idx not in range(len(weapons.WEAPONS)):
            raise ValueError(f"unknown weapon {weapon_query!r}")
        w = weapons.weapon_at(weapon_idx)
        player = ctx.resolve_player()
        if player:
            player.weapon = weapon_idx
            player.ammo[weapon_idx] = w.mag
            player.reserve[weapon_idx] = w.reserve
            ctx.print(f"[player] Equipped {w.name} with full ammo.")
            return {"weapon": w.name, "id": weapon_idx}
        ctx.print(f"[player] Equipped weapon {w.name} (client-side predicted).")
        return {"weapon": w.name, "id": weapon_idx}

    async def _cmd_player_teleport(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        x = float(args.get("x", 0.0))
        y = float(args.get("y", 0.0))
        z = float(args.get("z", 0.0))
        player = ctx.resolve_player()
        if player:
            player.state.x = x
            player.state.y = y
            player.state.z = z
            player.state.vel_x = 0.0
            player.state.vel_y = 0.0
            player.state.vel_z = 0.0
            ctx.print(f"[player] Teleported to ({x:.1f}, {y:.1f}, {z:.1f}).")
        else:
            ctx.print(f"[player] Teleported client to ({x:.1f}, {y:.1f}, {z:.1f}).")
        return {"x": x, "y": y, "z": z}

    async def _cmd_player_respawn(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        room = ctx.resolve_room()
        player = ctx.resolve_player()
        if room and player:
            room.respawn(player)
            ctx.print("[player] Respawned.")
            return {"respawned": True}
        ctx.print("[player] Sent respawn request.")
        return {"respawned": True}

    async def _cmd_player_get_pos(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        player = ctx.resolve_player()
        if player:
            st = player.state
            ctx.print(
                f"[player] Position: x={st.x:.2f}, y={st.y:.2f}, z={st.z:.2f} | "
                f"yaw={math.degrees(st.yaw):.1f}° pitch={math.degrees(st.pitch):.1f}° "
                f"ground={'YES' if st.on_ground else 'NO'}"
            )
            return {
                "x": st.x,
                "y": st.y,
                "z": st.z,
                "yaw": st.yaw,
                "pitch": st.pitch,
                "on_ground": st.on_ground,
            }
        ctx.print("[player] No live player in scope.")
        return {}

    async def _cmd_hitbox_get(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        spec = hitbox.get_spec()
        ctx.print(
            f"[hitbox] specId: {spec.spec_id[:12]} | radius: {spec.radius} | "
            f"eyeHeight: {spec.eye_height} | standingHeight: {spec.standing_height} | "
            f"headBand: {spec.head_band} | overridden: {spec.overridden}"
        )
        return spec.model_dump()

    async def _cmd_hitbox_tune(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        radius = args.get("radius")
        head_band = args.get("head_band")
        spec = hitbox.tune(
            radius=float(radius) if radius is not None else None,
            head_band=float(head_band) if head_band is not None else None,
        )
        ctx.print(
            f"[hitbox] Updated hitbox: radius={spec.radius}, head_band={spec.head_band} (specId: {spec.spec_id[:12]})"
        )
        return spec.model_dump()

    async def _cmd_hitbox_reset(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        spec = hitbox.reset_spec()
        ctx.print(f"[hitbox] Reset to canonical spec ({spec.spec_id[:12]}).")
        return spec.model_dump()

    async def _cmd_macro_run(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        name = str(args.get("name") or "").strip()
        macro = self.macros.get(name)
        if not macro:
            raise ValueError(f"macro {name!r} not found")
        ctx.print(f"[macro] Executing macro '{name}'...")
        return await ctx.exec_python_script(macro.code)

    async def _cmd_macro_list(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        ctx.print("Available Macros:")
        out = []
        for m in self.macros.values():
            tag = "[builtin]" if m.builtin else "[user]"
            ctx.print(f"  {tag} {m.name:18} - {m.description}")
            out.append(m.model_dump())
        return out

    async def _cmd_help(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        q = str(args.get("query") or "").strip().lower()
        ctx.print(
            f"=== hAssault Developer Console {'(matching: ' + q + ')' if q else ''} ==="
        )
        matched_cvars = [
            c
            for c in self.cvars.values()
            if not q or q in c.name.lower() or q in c.description.lower()
        ]
        matched_cmds = [
            cmd
            for cmd in self.commands.values()
            if not q or q in cmd.name.lower() or q in cmd.description.lower()
        ]

        if matched_cvars:
            ctx.print("--- Variables (CVars) ---")
            for c in sorted(matched_cvars, key=lambda x: x.name):
                flags = f" [{', '.join(c.flags)}]" if c.flags else ""
                ctx.print(
                    f"  {c.name:24} = {c.current_value!r} ({c.type}){flags} - {c.description}"
                )

        if matched_cmds:
            ctx.print("--- Commands ---")
            for cmd in sorted(matched_cmds, key=lambda x: x.name):
                ctx.print(f"  {cmd.signature or cmd.name:32} - {cmd.description}")

        return {
            "cvars": [c.model_dump() for c in matched_cvars],
            "commands": [cmd.model_dump() for cmd in matched_cmds],
        }

    # -------------------------------------------------------------------------
    # Execution Engine
    # -------------------------------------------------------------------------

    async def execute(self, req: ConsoleExecRequest) -> ConsoleExecResponse:
        line = req.command.strip()
        if not line:
            return ConsoleExecResponse(ok=True, command=line)

        ctx = ConsoleExecutionContext(self, req)

        # Check for multiple commands chained by semicolon
        # (unless inside quotes or multi-line python code)
        if ";" in line and "\n" not in line and not self._is_python_code(line):
            sub_commands = [c.strip() for c in line.split(";") if c.strip()]
            all_outputs: list[str] = []
            affected_cvars: dict[str, Any] = {}
            last_res = None
            for sub in sub_commands:
                sub_req = req.model_copy(update={"command": sub})
                res = await self.execute(sub_req)
                all_outputs.extend(res.output)
                affected_cvars.update(res.affected_cvars)
                last_res = res.result_data
                if not res.ok:
                    return ConsoleExecResponse(
                        ok=False,
                        command=line,
                        output=all_outputs,
                        error=res.error,
                        affected_cvars=affected_cvars,
                    )
            return ConsoleExecResponse(
                ok=True,
                command=line,
                output=all_outputs,
                affected_cvars=affected_cvars,
                result_data=last_res,
            )

        # 1. Try Python Syntax / Multi-line script first if it looks Pythonic
        if self._is_python_code(line):
            try:
                res_data = await ctx.exec_python_script(line)
                return ConsoleExecResponse(
                    ok=True,
                    command=line,
                    output=ctx.output_lines,
                    affected_cvars=ctx.affected_cvars,
                    result_data=res_data,
                )
            except Exception as exc:
                logger.exception("Python console exec failed")
                return ConsoleExecResponse(
                    ok=False,
                    command=line,
                    output=ctx.output_lines,
                    error=f"Python Error: {exc}",
                )

        # 2. Source-style command syntax: `cvar_or_cmd arg1 arg2` or `cvar=val`
        try:
            tokens = shlex.split(line)
        except Exception:
            tokens = line.split()

        if not tokens:
            return ConsoleExecResponse(ok=True, command=line)

        target = tokens[0]

        # Handle `cvar = value` or `cvar=value`
        if "=" in target:
            parts = target.split("=", 1)
            target = parts[0]
            tokens = [target, parts[1]] + tokens[1:]
        elif len(tokens) >= 3 and tokens[1] == "=":
            tokens = [tokens[0], tokens[2]] + tokens[3:]

        # A. Is it a CVar?
        if target in self.cvars:
            cvar = self.cvars[target]
            if len(tokens) == 1:
                # Query value
                ctx.print(
                    f'"{cvar.name}" is "{cvar.current_value}" '
                    f'(default "{cvar.default_value}")'
                    f" - {cvar.description}"
                )
                return ConsoleExecResponse(
                    ok=True,
                    command=line,
                    output=ctx.output_lines,
                    result_data=cvar.current_value,
                )
            # Setting value
            val_str = tokens[1]
            parsed_val = self._coerce_cvar_value(cvar, val_str)
            cvar.current_value = parsed_val
            ctx.affected_cvars[cvar.name] = parsed_val
            ctx.print(f"{cvar.name} = {parsed_val}")
            return ConsoleExecResponse(
                ok=True,
                command=line,
                output=ctx.output_lines,
                affected_cvars=ctx.affected_cvars,
                result_data=parsed_val,
            )

        # B. Is it a ConCommand?
        if target in self.handlers:
            handler = self.handlers[target]
            cmd_def = self.commands[target]
            parsed_args = self._parse_command_args(cmd_def, tokens[1:])
            try:
                res_data = await handler(parsed_args, ctx)
                return ConsoleExecResponse(
                    ok=True,
                    command=line,
                    output=ctx.output_lines,
                    affected_cvars=ctx.affected_cvars,
                    result_data=res_data,
                )
            except Exception as exc:
                return ConsoleExecResponse(
                    ok=False,
                    command=line,
                    output=ctx.output_lines,
                    error=f"Command Error: {exc}",
                )

        # C. Not found -> Try evaluate as bare python expression
        try:
            res_data = await ctx.exec_python_script(line)
            return ConsoleExecResponse(
                ok=True,
                command=line,
                output=ctx.output_lines,
                affected_cvars=ctx.affected_cvars,
                result_data=res_data,
            )
        except Exception:
            return ConsoleExecResponse(
                ok=False,
                command=line,
                output=ctx.output_lines,
                error=f"Unknown command or variable: '{target}'. Type 'help' for a list.",
            )

    def _is_python_code(self, code: str) -> bool:
        if (
            "\n" in code
            or code.startswith("for ")
            or code.startswith("if ")
            or code.startswith("def ")
            or code.startswith("import ")
        ):
            return True
        # Check if contains function call syntax like `server.start("map")` or assignments
        if "(" in code and ")" in code:
            return True
        if " = " in code or "==" in code or "+=" in code:
            return True
        return False

    def _coerce_cvar_value(self, cvar: CVarDefinition, raw: str) -> Any:
        clean = raw.strip().strip("'\"")
        if cvar.type == "boolean":
            return clean.lower() in ("1", "true", "yes", "on")
        if cvar.type == "number":
            val = float(clean)
            if cvar.min_value is not None:
                val = max(val, cvar.min_value)
            if cvar.max_value is not None:
                val = min(val, cvar.max_value)
            return (
                int(val)
                if val.is_integer() and isinstance(cvar.default_value, int)
                else val
            )
        if cvar.type == "enum":
            if cvar.enum_values and clean not in cvar.enum_values:
                # fuzzy match or default
                match = next(
                    (e for e in cvar.enum_values if clean.lower() == e.lower()), None
                )
                if match:
                    return match
                raise ValueError(f"value must be one of {', '.join(cvar.enum_values)}")
            return clean
        return clean

    def _parse_command_args(
        self, cmd: ConCommandDefinition, raw_tokens: list[str]
    ) -> dict[str, Any]:
        args: dict[str, Any] = {}
        pos_idx = 0
        for token in raw_tokens:
            if ":" in token and not token.startswith("http"):
                k, v = token.split(":", 1)
                args[k.strip()] = self._parse_scalar(v.strip())
            elif "=" in token:
                k, v = token.split("=", 1)
                args[k.strip()] = self._parse_scalar(v.strip())
            else:
                if pos_idx < len(cmd.parameters):
                    param_name = cmd.parameters[pos_idx].name
                    args[param_name] = self._parse_scalar(token)
                    pos_idx += 1
        return args

    def _parse_scalar(self, raw: str) -> Any:
        clean = raw.strip().strip("'\"")
        if clean.lower() == "true":
            return True
        if clean.lower() == "false":
            return False
        try:
            if "." in clean:
                return float(clean)
            return int(clean)
        except ValueError:
            return clean


# -----------------------------------------------------------------------------
# Execution Context & Python Proxy Sandbox
# -----------------------------------------------------------------------------


class ConsoleExecutionContext:
    """Carries output logs, affected CVars, room / player references, and
    provides a Python sandbox namespace."""

    def __init__(self, registry: ConsoleRegistry, req: ConsoleExecRequest) -> None:
        self.registry = registry
        self.req = req
        self.output_lines: list[str] = []
        self.affected_cvars: dict[str, Any] = {}
        self._eval_mode: bool = False

    def print(self, *items: Any) -> None:
        text = " ".join(str(i) for i in items)
        self.output_lines.append(text)

    def resolve_room(self, room_id: str | None = None) -> Any:
        rid = room_id or self.req.room_id
        if rid:
            r = match_server.get(rid)
            if r:
                return r
        rooms = list(match_server.rooms.values())
        if len(rooms) == 1:
            return rooms[0]
        return None

    def resolve_player(self) -> Any:
        room = self.resolve_room()
        if not room:
            return None
        pid = self.req.player_id
        if pid and pid in room.players:
            return room.players[pid]
        # Return first human player
        humans = [p for p in room.players.values() if not p.is_bot]
        return humans[0] if humans else None

    async def exec_python_script(self, code: str) -> Any:
        """Execute a Python snippet or macro within the game context."""
        sandbox = self._build_sandbox()

        # Capture print
        sandbox["print"] = self.print

        # Parse AST to check if it's a single expression vs statements
        try:
            parsed = ast.parse(code)
        except SyntaxError as exc:
            raise SyntaxError(f"Syntax error at line {exc.lineno}: {exc.msg}") from exc

        if len(parsed.body) == 1 and isinstance(parsed.body[0], ast.Expr):
            # Evaluate expression and return result
            self._eval_mode = True
            expr_ast = ast.Expression(parsed.body[0].value)
            res = eval(compile(expr_ast, "<console>", "eval"), sandbox)
            if asyncio.iscoroutine(res) or asyncio.isfuture(res):
                res = await res
            if res is not None:
                self.print(repr(res))
            return res

        # Execute statements
        self._eval_mode = False
        exec(compile(code, "<console>", "exec"), sandbox)
        # Yield to let any scheduled tasks run
        await asyncio.sleep(0)
        return None

    def _build_sandbox(self) -> dict[str, Any]:
        """Create namespace objects (net, draw, server, player, hitbox, physics, macro)."""

        class NamespaceProxy:
            def __init__(self, prefix: str, ctx: ConsoleExecutionContext) -> None:
                self._prefix = prefix
                self._ctx = ctx

            def __getattr__(self, item: str) -> Any:
                full_name = f"{self._prefix}.{item}" if self._prefix else item
                # Check for sub-namespace e.g. server.bots
                if full_name in ("server.bots", "draw.crosshair"):
                    return NamespaceProxy(full_name, self._ctx)
                # Check for CVar
                if full_name in self._ctx.registry.cvars:
                    return self._ctx.registry.cvars[full_name].current_value
                # Check for Command
                if full_name in self._ctx.registry.handlers:
                    handler = self._ctx.registry.handlers[full_name]

                    def _wrapper(*args: Any, **kwargs: Any) -> Any:
                        cmd_def = self._ctx.registry.commands[full_name]
                        # Map positional args to params
                        params = {
                            p.name: args[i]
                            for i, p in enumerate(cmd_def.parameters)
                            if i < len(args)
                        }
                        params.update(kwargs)
                        coro = handler(params, self._ctx)
                        if self._ctx._eval_mode:
                            return coro
                        try:
                            loop = asyncio.get_running_loop()
                            return loop.create_task(coro)
                        except RuntimeError:
                            return coro

                    return _wrapper
                raise AttributeError(
                    f"'{self._prefix}' has no property or command '{item}'"
                )

            def __setattr__(self, item: str, value: Any) -> None:
                if item.startswith("_"):
                    super().__setattr__(item, value)
                    return
                full_name = f"{self._prefix}.{item}" if self._prefix else item
                if full_name in self._ctx.registry.cvars:
                    cvar = self._ctx.registry.cvars[full_name]
                    coerced = self._ctx.registry._coerce_cvar_value(cvar, str(value))
                    cvar.current_value = coerced
                    self._ctx.affected_cvars[full_name] = coerced
                    self._ctx.print(f"[set] {full_name} = {coerced}")
                    return
                super().__setattr__(item, value)

        sandbox: dict[str, Any] = {
            "net": NamespaceProxy("net", self),
            "draw": NamespaceProxy("draw", self),
            "server": NamespaceProxy("server", self),
            "player": NamespaceProxy("player", self),
            "hitbox": NamespaceProxy("hitbox", self),
            "physics": NamespaceProxy("physics", self),
            "macro": NamespaceProxy("macro", self),
            "help": lambda q="": self.registry.handlers["help"]({"query": q}, self),
            "math": math,
            "json": json,
            "time": time,
        }
        return sandbox


# Global Console Registry instance
console_registry = ConsoleRegistry()
