"""Every `hassault.*` setting: its type, range, default and scope.

One table, served at `GET /api/hassault/settings/schema` by the node and by the
game server, and pinned by `settings-vectors.json`
(`scripts/gen_hassault_settings_vectors.py`):

- a vitest checks the pane manifest (`packages/core/.../hassault/index.ts`) has a
  row for every key here, with the same type and default — the Settings page and
  the in-game menus cannot show a different default from the one the game uses;
- a cargo test feeds each key the native client reads to `Settings::from_values`
  below, above and inside its range, and checks what comes back.

### Scope

`account` keys follow the player: aim, crosshair, binds, the debug overlays they
practise with. `machine` keys describe the computer: the GPU and API, the monitor
and display mode, render scale and quality, where the native binary is. When
account settings sync to the game server (P4), only `account` keys travel — a
4K monitor's render scale applied to a laptop is a setting that breaks the game.

### Not a validator for the bag

The bag stays a flat map of scalars that anything may write. Each client clamps
what it reads to these ranges; this table is the one place the ranges are
written down, so the clamps can be checked against it rather than against each
other.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.modules.hassault import crosshair


@dataclass(frozen=True)
class SettingSpec:
    key: str
    type: str  # "number" | "boolean" | "string"
    default: object
    scope: str  # "account" | "machine"
    min: float | None = None
    max: float | None = None
    #: A number that is snapped to one of these rather than clamped.
    choices: tuple[float, ...] | None = None
    integer: bool = False
    #: The strings a string row accepts; anything else reads as the default.
    enum: tuple[str, ...] | None = None
    #: A string row that also accepts `#rrggbb` (the crosshair colour).
    hex_color: bool = False
    #: A document rather than a value (the control map, the graphics overrides):
    #: no row on the Settings page, edited by its own surface.
    document: bool = False
    #: Read by the native client's `Settings::from_values`.
    native: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        d: dict = {
            "key": self.key,
            "type": self.type,
            "default": self.default,
            "scope": self.scope,
        }
        for name in ("min", "max"):
            if getattr(self, name) is not None:
                d[name] = getattr(self, name)
        if self.choices is not None:
            d["choices"] = list(self.choices)
        if self.integer:
            d["integer"] = True
        if self.enum is not None:
            d["enum"] = list(self.enum)
        if self.hex_color:
            d["hexColor"] = True
        if self.document:
            d["document"] = True
        if self.native:
            d["native"] = True
        if self.aliases:
            d["aliases"] = list(self.aliases)
        return d


def _num(key, default, scope, lo, hi, **kw) -> SettingSpec:
    return SettingSpec(key, "number", default, scope, min=lo, max=hi, **kw)


def _flag(key, default, scope, **kw) -> SettingSpec:
    return SettingSpec(key, "boolean", default, scope, **kw)


def _text(key, default, scope, **kw) -> SettingSpec:
    return SettingSpec(key, "string", default, scope, **kw)


def _xh(rng: tuple[int, int, int]) -> tuple[float, float]:
    lo, hi, k = rng
    return lo / k, hi / k


_D = crosshair.DEFAULT

SCHEMA: tuple[SettingSpec, ...] = (
    # ---- the player ----
    _num("hassault.sensitivity", 1, "account", 0.05, 10, native=True),
    # `hassault.fov` was the browser's row; read when this one is unset.
    _num(
        "hassault.video.fov",
        75,
        "account",
        60,
        110,
        native=True,
        aliases=("hassault.fov",),
    ),
    _num("hassault.volume", 0.7, "account", 0, 1),
    _flag("hassault.crouchToggle", False, "account"),
    _flag("hassault.voice.pushToTalk", False, "account"),
    _text("hassault.controls", "", "account", document=True),
    # `{t: {primary, secondary}, ct: {...}}` weapon ids, JSON (or the older flat
    # `{primary, secondary}` for both sides). Validated server-side slot by slot
    # and side by side (`weapons.loadouts_for`), so a stale id costs one slot.
    _text("hassault.loadout", "", "account", document=True),
    # Agent per side, `{t: id, ct: id}`, validated (`cosmetics.agents_for`).
    _text("hassault.agents", "", "account", document=True),
    _flag("hassault.debug.hitboxes", False, "account", native=True),
    _flag("hassault.debug.grenadeArc", False, "account", native=True),
    # ---- the crosshair (spec v1, `crosshair.py`) ----
    _text(
        "hassault.crosshair.style",
        _D.style,
        "account",
        enum=crosshair.STYLES,
        native=True,
    ),
    _num(
        "hassault.crosshair.size", _D.size, "account", *_xh(crosshair.SIZE), native=True
    ),
    _num("hassault.crosshair.gap", _D.gap, "account", *_xh(crosshair.GAP), native=True),
    _num(
        "hassault.crosshair.thickness",
        _D.thickness,
        "account",
        *_xh(crosshair.THICKNESS),
        native=True,
    ),
    _flag("hassault.crosshair.outline", True, "account", native=True),
    _num(
        "hassault.crosshair.outlineThickness",
        _D.outline,
        "account",
        *_xh(crosshair.OUTLINE),
        native=True,
    ),
    _num(
        "hassault.crosshair.alpha",
        _D.alpha,
        "account",
        *_xh(crosshair.ALPHA),
        native=True,
    ),
    _flag("hassault.crosshair.dot", _D.dot, "account", native=True),
    _flag("hassault.crosshair.tStyle", _D.t_style, "account", native=True),
    _flag("hassault.crosshair.dynamic", _D.dynamic, "account", native=True),
    _text(
        "hassault.crosshair.color",
        _D.color,
        "account",
        enum=crosshair.COLOR_NAMES,
        hex_color=True,
        native=True,
    ),
    # ---- this machine ----
    _flag("hassault.nativeClient", True, "machine"),
    _text(
        "hassault.graphics", "auto", "machine", enum=("auto", "low", "medium", "high")
    ),
    _text("hassault.graphics.custom", "{}", "machine", document=True),
    _text("hassault.nativeBinaryPath", "", "machine"),
    _flag("hassault.autoBuildNative", True, "machine"),
    _flag("hassault.video.fullscreen", True, "machine", native=True),
    _text(
        "hassault.video.displayMode",
        "borderless",
        "machine",
        enum=("borderless", "exclusive"),
        native=True,
    ),
    _num("hassault.video.monitor", 0, "machine", 0, 16, integer=True, native=True),
    _text("hassault.video.exclusiveMode", "", "machine", native=True),
    _num("hassault.video.renderScale", 1, "machine", 0.5, 2, native=True),
    _num("hassault.video.hudScale", 1, "machine", 0.75, 1.5, native=True),
    _text(
        "hassault.video.quality",
        "medium",
        "machine",
        enum=("low", "medium", "high", "ultra"),
        native=True,
    ),
    _flag("hassault.video.vsync", True, "machine", native=True),
    # The preset rows: defaults are what `medium` writes.
    _num("hassault.video.msaa", 1, "machine", 1, 8, choices=(1, 2, 4, 8), native=True),
    _flag("hassault.video.antialias", False, "machine", native=True),
    _num(
        "hassault.video.anisotropy",
        8,
        "machine",
        1,
        16,
        choices=(1, 2, 4, 8, 16),
        native=True,
    ),
    _text(
        "hassault.video.textureQuality",
        "medium",
        "machine",
        enum=("low", "medium", "high"),
        native=True,
    ),
    _flag("hassault.video.shadows", True, "machine", native=True),
    _text(
        "hassault.video.shadowQuality",
        "medium",
        "machine",
        enum=("low", "medium", "high", "ultra", "extreme"),
        native=True,
    ),
    _num(
        "hassault.video.mapLights",
        8,
        "machine",
        0,
        64,
        choices=(0, 8, 16, 32, 64),
        native=True,
    ),
    _num("hassault.video.bloom", 0, "machine", 0, 1, native=True),
    _flag("hassault.video.sky", True, "machine", native=True),
    _num("hassault.video.sharpen", 0, "machine", 0, 1, native=True),
    _num("hassault.video.brightness", 1, "machine", 0.6, 1.6, native=True),
    _num("hassault.video.frameLatency", 1, "machine", 1, 3, integer=True, native=True),
    _num(
        "hassault.video.fpsLimit",
        0,
        "machine",
        0,
        360,
        choices=(0, 60, 120, 144, 240, 360),
        native=True,
    ),
    _text("hassault.video.adapter", "auto", "machine", native=True),
    _text(
        "hassault.video.backend",
        "auto",
        "machine",
        enum=("auto", "dx12", "vulkan", "metal", "gl"),
        native=True,
    ),
    _text(
        "hassault.video.powerPreference",
        "high",
        "machine",
        enum=("high", "low"),
        native=True,
    ),
)

BY_KEY: dict[str, SettingSpec] = {s.key: s for s in SCHEMA}
VERSION = 1


def to_dict() -> dict:
    return {"version": VERSION, "settings": [s.to_dict() for s in SCHEMA]}


def account_keys() -> list[str]:
    return [s.key for s in SCHEMA if s.scope == "account"]
