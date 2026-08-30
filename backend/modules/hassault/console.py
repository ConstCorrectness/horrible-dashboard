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
from backend.modules.hassault import (
    assets,
    bots,
    drafts,
    hitbox,
    pickups,
    textures,
    weapons,
)
from backend.modules.hassault.cgz import ENTITY_NAMES, CgzError
from backend.modules.hassault.physics import World as SimWorld
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


def _edit_value(field: str, raw: Any) -> Any:
    """Coerce a console argument into what a map document field wants.

    A colour is the case that needs it: `edit.ent.set(field='color',
    value='255,240,206')` is how you say a colour on a command line, and the
    document wants `[255, 240, 206]`. Everything else is already a scalar by the
    time it gets here.
    """
    if field in ("color", "watercolor", "rect", "attrs") and isinstance(raw, str):
        parts = [p.strip() for p in raw.replace("[", "").replace("]", "").split(",")]
        try:
            return [int(p) for p in parts if p]
        except ValueError:
            raise ValueError(f"{field} wants a list of numbers, got {raw!r}") from None
    return raw


def _split_named(token: str) -> tuple[str, str, str]:
    """Split `k:v` / `k=v` on whichever separator comes first, or `("", "", token)`.

    Whichever comes *first* matters: `radius=1:2` is the key `radius`, and
    splitting on the colon instead would invent a key of `radius=1`.
    """
    colon, equals = token.find(":"), token.find("=")
    candidates = [i for i in (colon, equals) if i > 0]
    if not candidates:
        return "", "", token
    at = min(candidates)
    return token[:at].strip(), token[at], token[at + 1 :].strip()


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
        #: The map being edited, and what is selected in it. Process-global for
        #: the reason the match server is: this is one person's node, and
        #: threading a draft id through every `edit.*` command would be ceremony
        #: around a number that is always the same one. The native client's
        #: crosshair writes the same selection the typed commands read.
        self.active_draft: str = ""
        self.selection: dict[str, int] = {}
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
            # The range is `settings::FOV_RANGE` in the native client, and the two
            # have to agree: the console and the video menu write the same FOV,
            # so a console that accepted 65 would set a value the menu cannot
            # show and the next nudge of that row would silently jump it to 70.
            "Vertical field of view in degrees (70-120)",
            min_val=70.0,
            max_val=120.0,
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
            description="Equip a weapon by name or slot, with full ammo",
            signature="player.give(weapon: str)",
            params=[
                ConCommandParameter(
                    name="weapon",
                    type="enum",
                    required=True,
                    # Derived from the weapon table, not written out again. The
                    # hardcoded list this replaces offered `carbine` and `subgun`
                    # — neither has ever been a weapon here — and omitted
                    # `assault`, which is one. This registry is *served*, so that
                    # list was the autocomplete in both clients.
                    enum_values=weapons.weapon_slots(),
                    description="A weapon id, its display name, or its slot number.",
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

        # ---- the map designer -------------------------------------------------
        #
        # Declared here rather than anywhere client-side, because this registry is
        # *served*: `GET /console/definitions` hands the list to the native client
        # and to the browser's console pane, and both render what they are given.
        # So the editor's typed half arrives in two clients at once, and neither
        # can offer a command the node has never heard of.
        #
        # The commands operate on **the active draft**, which is process-global
        # for the same reason the match server is: this is one person's node, and
        # threading a draft id through every command would be ceremony around a
        # number that is always the same one.

        self._register_command(
            "edit.new",
            "edit",
            self._cmd_edit_new,
            description="Open a blank map for editing",
            signature="edit.new(name: str = '')",
            example="edit.new(name='arena')",
            params=[ConCommandParameter(name="name", type="string", default="")],
        )
        self._register_command(
            "edit.open",
            "edit",
            self._cmd_edit_open,
            description="Open one of this app's maps for editing",
            signature="edit.open(map: str)",
            example="edit.open(map='hd_pit')",
            params=[
                ConCommandParameter(
                    name="map",
                    type="string",
                    required=True,
                    description="A bundled map name. Only ours have a source document to edit.",
                )
            ],
        )
        self._register_command(
            "edit.close",
            "edit",
            self._cmd_edit_close,
            description="Discard the active draft without saving",
            signature="edit.close()",
            example="edit.close()",
        )
        self._register_command(
            "edit.status",
            "edit",
            self._cmd_edit_status,
            description="What is being edited, and what is selected",
            signature="edit.status()",
            example="edit.status()",
        )
        self._register_command(
            "edit.save",
            "edit",
            self._cmd_edit_save,
            description="Write the draft to its JSON brush list",
            signature="edit.save(name: str = '', overwrite: bool = False)",
            example="edit.save(name='arena')",
            params=[
                ConCommandParameter(name="name", type="string", default=""),
                ConCommandParameter(name="overwrite", type="boolean", default=False),
            ],
        )
        self._register_command(
            "edit.list",
            "edit",
            self._cmd_edit_list,
            description="The draft's brushes in paint order, or its entities",
            signature="edit.list(what: str = 'brushes')",
            example="edit.list(what='entities')",
            params=[
                ConCommandParameter(
                    name="what",
                    type="enum",
                    default="brushes",
                    enum_values=["brushes", "entities"],
                )
            ],
        )
        self._register_command(
            "edit.select",
            "edit",
            self._cmd_edit_select,
            description="Choose the brush or entity the set/remove commands act on",
            signature="edit.select(what: str, index: int)",
            example="edit.select(what='brush', index=3)",
            params=[
                ConCommandParameter(
                    name="what",
                    type="enum",
                    required=True,
                    enum_values=["brush", "entity"],
                ),
                ConCommandParameter(name="index", type="integer", required=True),
            ],
        )
        self._register_command(
            "edit.brush.add",
            "edit",
            self._cmd_edit_brush_add,
            description="Paint a room, a block of solid rock, or a staircase",
            signature="edit.brush.add(op, x, y, w, h, floor=0, ceil=16)",
            example="edit.brush.add(op='room', x=8, y=8, w=24, h=16, ceil=14)",
            params=[
                ConCommandParameter(
                    name="op",
                    type="enum",
                    required=True,
                    enum_values=["room", "solid", "stairs"],
                ),
                ConCommandParameter(name="x", type="integer", required=True),
                ConCommandParameter(name="y", type="integer", required=True),
                ConCommandParameter(name="w", type="integer", required=True),
                ConCommandParameter(name="h", type="integer", required=True),
                ConCommandParameter(name="floor", type="integer", default=0),
                ConCommandParameter(name="ceil", type="integer", default=16),
                ConCommandParameter(
                    name="to",
                    type="integer",
                    description="Stairs only: the height the run climbs to.",
                ),
            ],
        )
        self._register_command(
            "edit.brush.set",
            "edit",
            self._cmd_edit_brush_set,
            description="Change one field on the selected brush",
            signature="edit.brush.set(field: str, value, index: int = -1)",
            example="edit.brush.set(field='ceil', value=18)",
            params=[
                ConCommandParameter(name="field", type="string", required=True),
                ConCommandParameter(name="value", type="string", required=True),
                ConCommandParameter(name="index", type="integer", default=-1),
            ],
        )
        self._register_command(
            "edit.brush.remove",
            "edit",
            self._cmd_edit_brush_remove,
            description="Delete the selected brush",
            signature="edit.brush.remove(index: int = -1)",
            example="edit.brush.remove()",
            params=[ConCommandParameter(name="index", type="integer", default=-1)],
        )
        self._register_command(
            "edit.ent.add",
            "edit",
            self._cmd_edit_ent_add,
            description="Place a spawn, a light, an item or a ladder",
            signature="edit.ent.add(type: str, x: int, y: int)",
            example="edit.ent.add(type='playerstart', x=12, y=12)",
            params=[
                ConCommandParameter(name="type", type="string", required=True),
                ConCommandParameter(name="x", type="integer", required=True),
                ConCommandParameter(name="y", type="integer", required=True),
            ],
        )
        self._register_command(
            "edit.ent.set",
            "edit",
            self._cmd_edit_ent_set,
            description="Change one field on the selected entity",
            signature="edit.ent.set(field: str, value, index: int = -1)",
            example="edit.ent.set(field='radius', value=112)",
            params=[
                ConCommandParameter(name="field", type="string", required=True),
                ConCommandParameter(name="value", type="string", required=True),
                ConCommandParameter(name="index", type="integer", default=-1),
            ],
        )
        self._register_command(
            "edit.ent.remove",
            "edit",
            self._cmd_edit_ent_remove,
            description="Delete the selected entity",
            signature="edit.ent.remove(index: int = -1)",
            example="edit.ent.remove()",
            params=[ConCommandParameter(name="index", type="integer", default=-1)],
        )
        self._register_command(
            "edit.undo",
            "edit",
            self._cmd_edit_undo,
            description="Walk the draft back one edit",
            signature="edit.undo()",
            example="edit.undo()",
        )
        self._register_command(
            "edit.redo",
            "edit",
            self._cmd_edit_redo,
            description="Replay the edit undo took back",
            signature="edit.redo()",
            example="edit.redo()",
        )
        self._register_command(
            "edit.lint",
            "edit",
            self._cmd_edit_lint,
            description="Would this map play? Every check the bundled suite makes",
            signature="edit.lint()",
            example="edit.lint()",
        )
        self._register_command(
            "edit.textures",
            "edit",
            self._cmd_edit_textures,
            description="The texture palette, by name and slot",
            signature="edit.textures(group: str = '')",
            example="edit.textures(group='floor')",
            params=[ConCommandParameter(name="group", type="string", default="")],
        )
        self._register_command(
            "edit.playtest",
            "edit",
            self._cmd_edit_playtest,
            description="Load the draft into a match on this node and walk it",
            signature="edit.playtest()",
            example="edit.playtest()",
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
        parsed = assets.load_map(map_name)
        if parsed is None:
            raise ValueError(f"no map named {map_name!r}")
        room = ctx.resolve_room()
        if room is None:
            room = match_server.create(map_name)
            ctx.print(f"[server] Started new match on '{map_name}' (room {room.id}).")
            return {"room": room.id, "map": map_name}
        # Everything `match_server.create` derives from a parsed map, derived the
        # same way. This used to assign the `CgzMap` itself to `room.world` — the
        # simulation wants a `physics.World` — and to call an `assets.player_spawns`
        # that has never existed, so changing a live room's map raised
        # `AttributeError` before it could reach the type error behind it. The
        # items were never swapped at all, which would have left the old map's
        # pickups lying at their old coordinates on the new one.
        world = SimWorld.from_map(parsed)
        room.map_name = map_name
        room.world = world
        room.spawns = parsed.spawns()
        room.items = pickups.Field(items=pickups.place(world, parsed.entities))
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
        # `is None`, never falsiness: the parser turns "0" into the integer 0,
        # and `0 or ""` is `""` — so `player.give 0` silently handed out the
        # default instead of the knife, which is slot 0. The one argument this
        # command takes has a legitimate falsy value.
        raw = args.get("weapon")
        weapon_query = "" if raw is None else str(raw).strip()
        # Resolved against the weapon table itself. This used to read a
        # `weapons.WEAPON_NAMES` that has never existed, so the command failed
        # with an `AttributeError` every time it was run — and defaulted to a
        # "carbine" that is not a weapon here either.
        weapon_idx = weapons.resolve_slot(weapon_query or "assault")
        if weapon_idx is None:
            raise ValueError(
                f"unknown weapon {weapon_query!r}; try one of "
                + ", ".join(weapons.weapon_slots())
            )
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
        names = {p.name for p in cmd.parameters}
        pos_idx = 0
        for token in raw_tokens:
            # `k:v` and `k=v` are named arguments only when `k` is a parameter
            # this command actually has. Anything else is a positional value that
            # happens to contain the separator — `draft:9f2c` is a map name, not
            # an argument called `draft`, and `server.map` silently lost its only
            # parameter to that reading. The old rule exempted one prefix
            # (`http`), which is the same bug with a shorter list: the parser
            # already knows the parameter names, so it can just ask.
            key, separator, value = _split_named(token)
            if separator and key in names:
                args[key] = self._parse_scalar(value)
            elif pos_idx < len(cmd.parameters):
                args[cmd.parameters[pos_idx].name] = self._parse_scalar(token)
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

    # -------------------------------------------------------------------------
    # Map Designer
    # -------------------------------------------------------------------------
    #
    # The console half of the editor. The pointing half — fly the map, put the
    # crosshair on a wall, drag it — lives in the native client; this is the half
    # that wants exact numbers, and it works from either client because the
    # registry above is served rather than duplicated.

    def _edit_draft(self) -> drafts.Draft:
        if not self.active_draft:
            raise ValueError("nothing is open; run edit.open or edit.new first")
        try:
            return drafts.require(self.active_draft)
        except drafts.DraftError:
            # The draft was swept or closed underneath us. Say so plainly rather
            # than reporting "no open draft <id>", which reads as a bug.
            self.active_draft = ""
            raise ValueError(
                "the draft that was open is gone; open one again"
            ) from None

    def _edit_apply(self, ctx: ConsoleExecutionContext, edit: dict[str, Any]) -> Any:
        """Apply an edit and report what it did to the map, not just that it ran.

        The lint summary rides along on every edit for the reason the whole
        designer exists: a brush that seals a corridor looks completely ordinary
        in a document, and finding out at save time is finding out too late.
        """
        draft = self._edit_draft()
        try:
            drafts.apply(draft.id, edit)
        except (drafts.DraftError, CgzError) as exc:
            raise ValueError(str(exc)) from None
        findings = drafts.lint(draft.id)
        errors = [f for f in findings if f.severity == "error"]
        ctx.print(f"[edit] rev {draft.revision}: {edit['op']}")
        for finding in findings[:6]:
            ctx.print(f"[edit]   {finding.severity}: {finding.message}")
        if len(findings) > 6:
            ctx.print(f"[edit]   ... and {len(findings) - 6} more (edit.lint)")
        return {
            "revision": draft.revision,
            "errors": len(errors),
            "warnings": len(findings) - len(errors),
        }

    def _edit_index(self, args: dict[str, Any], items: list, what: str) -> int:
        """The index a command acts on: an explicit one, else the selection."""
        raw = args.get("index")
        index = int(raw) if raw is not None else -1
        if index < 0:
            index = self.selection.get(what, -1)
        if not 0 <= index < len(items):
            raise ValueError(
                f"no {what} {index} to act on; select one with edit.select "
                f"(there are {len(items)})"
            )
        return index

    async def _cmd_edit_new(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = drafts.create()
        if name := str(args.get("name") or "").strip():
            drafts.apply(draft.id, {"op": "map.set", "key": "title", "value": name})
            draft.name = name
        self.active_draft = draft.id
        self.selection = {}
        ctx.print(
            f"[edit] New map open as '{drafts.PREFIX}{draft.id}'. It is solid rock — "
            "carve a room with edit.brush.add."
        )
        return {"draft": draft.id, "map": drafts.PREFIX + draft.id}

    async def _cmd_edit_open(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        name = str(args.get("map") or args.get("name") or "").strip()
        if not name:
            raise ValueError("map name required")
        try:
            draft = drafts.create(name)
        except drafts.DraftError as exc:
            raise ValueError(str(exc)) from None
        self.active_draft = draft.id
        self.selection = {}
        ctx.print(
            f"[edit] Editing {name} as '{drafts.PREFIX}{draft.id}' — "
            f"{len(draft.doc.get('brushes', []))} brushes, "
            f"{len(draft.doc.get('entities', []))} entities."
        )
        return {"draft": draft.id, "map": drafts.PREFIX + draft.id, "from": name}

    async def _cmd_edit_close(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        drafts.close(draft.id)
        self.active_draft = ""
        self.selection = {}
        ctx.print("[edit] Draft discarded. Anything unsaved is gone.")
        return {"closed": draft.id}

    async def _cmd_edit_status(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        if not self.active_draft:
            ctx.print("[edit] Nothing open. edit.open(map='hd_pit') or edit.new().")
            return {"draft": None}
        draft = self._edit_draft()
        findings = drafts.lint(draft.id)
        errors = sum(1 for f in findings if f.severity == "error")
        ctx.print(
            f"[edit] {drafts.PREFIX}{draft.id} (from {draft.name or 'nothing'}) "
            f"rev {draft.revision} — {len(draft.doc.get('brushes', []))} brushes, "
            f"{len(draft.doc.get('entities', []))} entities, "
            f"{errors} errors / {len(findings) - errors} warnings"
        )
        if self.selection:
            ctx.print(f"[edit] selected: {self.selection}")
        return {
            "draft": draft.id,
            "map": drafts.PREFIX + draft.id,
            "revision": draft.revision,
            "selection": dict(self.selection),
            "errors": errors,
        }

    async def _cmd_edit_save(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        name = str(args.get("name") or "").strip() or None
        try:
            saved = drafts.save(
                draft.id, name, overwrite=bool(args.get("overwrite", False))
            )
        except (drafts.DraftError, CgzError) as exc:
            raise ValueError(str(exc)) from None
        findings = drafts.lint(draft.id)
        errors = sum(1 for f in findings if f.severity == "error")
        ctx.print(f"[edit] Saved as {saved}.json — a brush list, not a binary.")
        if errors:
            # Saved anyway, and told plainly. A map somebody is halfway through
            # making is still worth keeping; refusing to save it would break the
            # editor exactly when it is most useful.
            ctx.print(f"[edit] {errors} playability errors remain — run edit.lint.")
        return {"saved": saved, "errors": errors}

    async def _cmd_edit_list(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        what = str(args.get("what") or "brushes").lower()
        if what.startswith("ent"):
            rows = draft.doc.get("entities", [])
            for index, row in enumerate(rows):
                extra = {k: v for k, v in row.items() if k != "type"}
                ctx.print(f"[edit] {index:3} {row.get('type', '?'):12} {extra}")
        else:
            what = "brushes"
            rows = draft.doc.get("brushes", [])
            for index, row in enumerate(rows):
                # Paint order matters — a later brush overwrites an earlier one —
                # so the index is the useful part of this listing, not decoration.
                extra = {k: v for k, v in row.items() if k not in ("op", "rect")}
                ctx.print(
                    f"[edit] {index:3} {row.get('op', '?'):7} "
                    f"rect={row.get('rect')} {extra}"
                )
        if not rows:
            ctx.print(f"[edit] no {what} yet")
        return {what: rows}

    async def _cmd_edit_select(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        what = "entity" if str(args.get("what", "")).startswith("ent") else "brush"
        items = draft.doc.get("brushes" if what == "brush" else "entities", [])
        index = int(args.get("index", -1))
        if not 0 <= index < len(items):
            raise ValueError(f"no {what} at {index} (there are {len(items)})")
        self.selection[what] = index
        ctx.print(f"[edit] selected {what} {index}: {items[index]}")
        return {"selected": what, "index": index, "value": items[index]}

    async def _cmd_edit_brush_add(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        op = str(args.get("op") or "room").lower()
        brush: dict[str, Any] = {
            "op": op,
            "rect": [
                int(args.get("x", 0)),
                int(args.get("y", 0)),
                int(args.get("w", 1)),
                int(args.get("h", 1)),
            ],
        }
        # `solid` stores only wtex — everything else on it would be a field the
        # writer refuses, so the extras are simply not offered for it.
        if op != "solid":
            brush["ceil"] = int(args.get("ceil", 16))
            if op == "stairs":
                # Stairs climb *between* two heights, so `floor` is where the run
                # starts and `to` is where it ends. Defaulting `to` to `floor`
                # makes a flat run rather than an error, which is a shape you can
                # then drag into place.
                brush["from"] = int(args.get("floor", 0))
                brush["to"] = int(args.get("to", args.get("floor", 0)))
            else:
                brush["floor"] = int(args.get("floor", 0))
        result = self._edit_apply(ctx, {"op": "brush.add", "brush": brush})
        self.selection["brush"] = len(draft.doc["brushes"]) - 1
        result["index"] = self.selection["brush"]
        return result

    async def _cmd_edit_brush_set(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        index = self._edit_index(args, draft.doc.get("brushes", []), "brush")
        field = str(args.get("field") or "").strip()
        if not field:
            raise ValueError("field required")
        return self._edit_apply(
            ctx,
            {
                "op": "brush.update",
                "index": index,
                "patch": {field: _edit_value(field, args.get("value"))},
            },
        )

    async def _cmd_edit_brush_remove(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        index = self._edit_index(args, draft.doc.get("brushes", []), "brush")
        self.selection.pop("brush", None)
        return self._edit_apply(ctx, {"op": "brush.remove", "index": index})

    async def _cmd_edit_ent_add(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        kind = str(args.get("type") or "").strip()
        if kind not in ENTITY_NAMES:
            raise ValueError(
                f"unknown entity type {kind!r}; try one of "
                + ", ".join(n for n in ENTITY_NAMES if n != "notused")
            )
        entity: dict[str, Any] = {
            "type": kind,
            "x": int(args.get("x", 0)),
            "y": int(args.get("y", 0)),
        }
        # No z: `mapsource` resolves it from the floor actually built underneath,
        # which is the whole reason a spawn moved in the source does not silently
        # keep an old height.
        result = self._edit_apply(ctx, {"op": "ent.add", "entity": entity})
        self.selection["entity"] = len(draft.doc["entities"]) - 1
        result["index"] = self.selection["entity"]
        return result

    async def _cmd_edit_ent_set(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        index = self._edit_index(args, draft.doc.get("entities", []), "entity")
        field = str(args.get("field") or "").strip()
        if not field:
            raise ValueError("field required")
        return self._edit_apply(
            ctx,
            {
                "op": "ent.update",
                "index": index,
                "patch": {field: _edit_value(field, args.get("value"))},
            },
        )

    async def _cmd_edit_ent_remove(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        index = self._edit_index(args, draft.doc.get("entities", []), "entity")
        self.selection.pop("entity", None)
        return self._edit_apply(ctx, {"op": "ent.remove", "index": index})

    async def _cmd_edit_undo(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        try:
            drafts.undo(draft.id)
        except drafts.DraftError as exc:
            raise ValueError(str(exc)) from None
        self.selection = {}
        ctx.print(f"[edit] undone; rev {draft.revision}")
        return {"revision": draft.revision}

    async def _cmd_edit_redo(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        try:
            drafts.redo(draft.id)
        except drafts.DraftError as exc:
            raise ValueError(str(exc)) from None
        self.selection = {}
        ctx.print(f"[edit] redone; rev {draft.revision}")
        return {"revision": draft.revision}

    async def _cmd_edit_lint(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        draft = self._edit_draft()
        findings = drafts.lint(draft.id)
        if not findings:
            ctx.print("[edit] Clean — this map clears the bar the bundled ones do.")
            return {"findings": []}
        for finding in findings:
            where = ""
            if finding.cells:
                first = finding.cells[0]
                where = f" (first at {first[0]},{first[1]})"
            ctx.print(f"[edit] {finding.severity}: {finding.message}{where}")
        return {"findings": [f.to_dict() for f in findings]}

    async def _cmd_edit_textures(
        self, args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        group = str(args.get("group") or "").strip().lower()
        rows = [t for t in textures.catalog() if not group or t["group"] == group]
        for row in rows:
            ctx.print(
                f"[edit] {row['id']:3}  {row['name']:18} {row['group']:10} "
                f"{row['pattern']:9} {row['color']}"
            )
        if not rows:
            groups = sorted({t["group"] for t in textures.catalog()})
            ctx.print(f"[edit] no group {group!r}; try {', '.join(groups)}")
        return {"textures": rows}

    async def _cmd_edit_playtest(
        self, _args: dict[str, Any], ctx: ConsoleExecutionContext
    ) -> Any:
        """Walk the draft. The only way to find out a ledge is one cube too high.

        The draft is addressed as a map, so this is `server.map` with no special
        handling anywhere — the match server loads `draft:<id>` through the same
        `assets.load_map` every other map goes through.
        """
        draft = self._edit_draft()
        map_name = drafts.PREFIX + draft.id
        room = ctx.resolve_room()
        if room is None:
            room = match_server.create(map_name)
            ctx.print(f"[edit] Playtesting in a new match, room {room.id}.")
        else:
            world = SimWorld.from_map(drafts.compiled(draft.id))
            parsed = drafts.compiled(draft.id)
            room.map_name = map_name
            room.world = world
            room.spawns = parsed.spawns()
            room.items = pickups.Field(items=pickups.place(world, parsed.entities))
            for player in room.players.values():
                room.respawn(player)
            ctx.print(f"[edit] Room {room.id} is now on the draft.")
        errors = sum(1 for f in drafts.lint(draft.id) if f.severity == "error")
        if errors:
            ctx.print(f"[edit] Note: {errors} playability errors — see edit.lint.")
        return {"room": room.id, "map": map_name, "errors": errors}


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
