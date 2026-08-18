"""The Windows virtual-audio adapter: VB-Audio Voicemeeter over its Remote API.

**Why ctypes and not a package.** `VoicemeeterRemote64.dll` ships *with
Voicemeeter*, so it exists only on machines that already installed it. Taking a
PyPI wrapper as a dependency would put a Windows-only, install-only concern into
`pyproject.toml` for every platform, to save maybe eighty lines. The API we
actually need is nine entry points, so we bind them here and the module has no
new dependency at all — the same shape as `hardware.probe` shelling out to
`nvidia-smi`.

**Why Voicemeeter at all, when the mixer already routes.** The frontend graph
(`packages/core/src/modules/audio/`) can send any dashboard audio to any output
device, and if one of those devices is a virtual cable that covers the "send my
audio to another app" case without this file existing. What it *cannot* do is
route audio the dashboard does not own: Spotify, a game, a call in another
browser. Voicemeeter can, and it is scriptable, so this adapter is the one place
the dashboard reaches out and rearranges the machine's own mixer.

**Three states, never two.** Following `hardware.probe`: found it, looked and it
is not installed, and *could not ask*. "Not on Windows" and "the registry key is
missing" and "the DLL refused to load" are all the third state, and rendering any
of them as "you don't have Voicemeeter" is the failure this module exists to
avoid — a user staring at a running Voicemeeter being told it isn't there.

**The API is stateful, single-client and not thread-safe.** `VBVMR_Login` binds
this *process* to the running mixer; a second login without a logout returns -2.
So there is one module-global client behind one lock, logged in lazily and logged
out at shutdown, and every call goes through `_call` while holding it. Callers
reach it through `asyncio.to_thread` — the DLL blocks.
"""

from __future__ import annotations

import ctypes
import logging
import platform
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Registry key holding the install path. The GUID is a fixed VB-Audio product
#: code, identical across Voicemeeter / Banana / Potato — they are one installer
#: with three executables, which is also why the *kind* has to be asked of the
#: running mixer rather than inferred from what is on disk.
_UNINSTALL_KEY = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\VB:Voicemeeter {17359A74-1236-5467}"

#: Fallback install locations, tried when the registry read fails (a user who
#: copied the folder, or a registry hive we cannot open). Never the only source:
#: hardcoding these *as* the answer is how you report "not installed" on a
#: machine that put Program Files somewhere else.
_FALLBACK_DIRS = (
    r"C:\Program Files (x86)\VB\Voicemeeter",
    r"C:\Program Files\VB\Voicemeeter",
)

#: `VBVMR_GetVoicemeeterType` values → (name, strip count, bus count, physical
#: strip count, physical bus count).
#:
#: The counts matter and must never be assumed. Banana is the version everyone
#: writes tutorials about, but this is a *different program* per value: a matrix
#: sized for Banana silently ignores three of Potato's strips, and one sized for
#: Potato writes `Strip[7].A1` on Banana, which the API rejects and which reads
#: as "the routing didn't take" with nothing in the UI to explain it.
KINDS: dict[int, tuple[str, int, int, int, int]] = {
    1: ("Voicemeeter", 3, 2, 2, 1),
    2: ("Voicemeeter Banana", 5, 5, 3, 3),
    3: ("Voicemeeter Potato", 8, 8, 5, 5),
}

#: `VBVMR_Login` return codes that mean "you are connected". 0 is the happy path;
#: **1 means logged in but the mixer is not running** — a distinction worth
#: keeping, because it is the one state we can fix ourselves (`run_voicemeeter`).
_LOGIN_OK = 0
_LOGIN_NOT_RUNNING = 1

#: The Remote API's string buffers are documented as 512 bytes. Anything shorter
#: is a buffer overrun in a C DLL, not a truncated Python string.
_STR_BUF = 512

#: How long to wait for a freshly launched Voicemeeter to answer. Launching is
#: asynchronous — `VBVMR_RunVoicemeeter` returns immediately and the audio engine
#: takes a moment to come up, during which every parameter read returns garbage.
_LAUNCH_TIMEOUT = 8.0


class VoicemeeterError(RuntimeError):
    """A Remote API call failed. Carries the raw return code for the log."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message if code is None else f"{message} (code {code})")
        self.code = code


@dataclass
class Strip:
    """One input strip: a hardware input or a virtual (application) input.

    `sends` is the row of the matrix — which buses this strip feeds — keyed by
    bus name (`A1`, `B1`, …). That is exactly the row of buttons under a fader in
    the Voicemeeter UI, and the whole reason this adapter exists.
    """

    index: int
    name: str
    label: str
    is_virtual: bool
    gain: float
    muted: bool
    sends: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "label": self.label,
            "isVirtual": self.is_virtual,
            "gain": round(self.gain, 1),
            "muted": self.muted,
            "sends": dict(self.sends),
        }


@dataclass
class Bus:
    """One output bus. `is_virtual` is the B-bus flag — the buses another
    application can select as a *recording* device, which is what makes "play
    this through my microphone" work at all."""

    index: int
    name: str
    label: str
    is_virtual: bool
    gain: float
    muted: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "label": self.label,
            "isVirtual": self.is_virtual,
            "gain": round(self.gain, 1),
            "muted": self.muted,
        }


@dataclass
class MixerState:
    """A whole Voicemeeter snapshot: the matrix, both axes, and what it is."""

    kind: str
    kind_id: int
    version: str
    strips: list[Strip]
    buses: list[Bus]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "kindId": self.kind_id,
            "version": self.version,
            "strips": [s.to_dict() for s in self.strips],
            "buses": [b.to_dict() for b in self.buses],
        }


def bus_names(kind_id: int) -> list[str]:
    """The bus names for a Voicemeeter kind, in UI order: physical A buses then
    virtual B buses. Derived from the kind rather than listed per version, so a
    future Voicemeeter with more buses needs one line in `KINDS`, not a new list.
    """
    _, _, buses, _, physical = KINDS.get(kind_id, KINDS[2])
    names = [f"A{i + 1}" for i in range(physical)]
    names += [f"B{i + 1}" for i in range(buses - physical)]
    return names


def install_dir() -> Path | None:
    """Where Voicemeeter is installed, or None if we could not determine it.

    Registry first, then the conventional locations. None here means *unknown*,
    which the caller must not collapse into "not installed" — see the module
    docstring.
    """
    if platform.system() != "Windows":
        return None

    try:
        import winreg  # noqa: PLC0415 — Windows-only, imported where it is used.

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, _UNINSTALL_KEY) as key:
                    value, _ = winreg.QueryValueEx(key, "UninstallString")
            except OSError:
                continue
            # The uninstall string is the *setup executable* sitting in the
            # install directory, so its parent is what we want.
            candidate = Path(str(value).strip('"')).parent
            if candidate.is_dir():
                return candidate
    except Exception as exc:  # pragma: no cover - registry shapes vary
        logger.debug("voicemeeter: registry lookup failed: %s", exc)

    for raw in _FALLBACK_DIRS:
        candidate = Path(raw)
        if candidate.is_dir():
            return candidate
    return None


def dll_path() -> Path | None:
    """The Remote API DLL matching this *interpreter's* bitness.

    Not the machine's: a 32-bit Python cannot load the 64-bit DLL, and the error
    it gets ("%1 is not a valid Win32 application") looks nothing like the real
    problem. Both DLLs ship side by side precisely so the client chooses.
    """
    directory = install_dir()
    if directory is None:
        return None
    name = (
        "VoicemeeterRemote64.dll"
        if ctypes.sizeof(ctypes.c_void_p) == 8
        else "VoicemeeterRemote.dll"
    )
    candidate = directory / name
    return candidate if candidate.is_file() else None


class _Client:
    """The process-global Remote API client.

    One instance, one lock, lazily logged in. Every public function in this
    module is a thin wrapper that takes the lock — the DLL keeps per-process
    state and is documented as not thread-safe, and FastAPI will happily call
    two routes at once.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._dll: ctypes.CDLL | None = None
        self._logged_in = False
        self._load_error: str | None = None

    # -- lifecycle ---------------------------------------------------------

    def _load(self) -> ctypes.CDLL:
        if self._dll is not None:
            return self._dll
        path = dll_path()
        if path is None:
            raise VoicemeeterError(
                "Voicemeeter is not installed (Remote API DLL not found)"
            )
        try:
            # `WinDLL` (stdcall) is wrong here — the Remote API is cdecl on both
            # bitnesses. On x64 the two conventions coincide, so this only
            # matters on 32-bit, where WinDLL corrupts the stack instead of
            # failing, and the symptom is a crash several calls later.
            dll = ctypes.CDLL(str(path))
        except OSError as exc:
            self._load_error = str(exc)
            raise VoicemeeterError(f"could not load {path.name}: {exc}") from exc

        dll.VBVMR_Login.restype = ctypes.c_long
        dll.VBVMR_Logout.restype = ctypes.c_long
        dll.VBVMR_RunVoicemeeter.argtypes = [ctypes.c_long]
        dll.VBVMR_RunVoicemeeter.restype = ctypes.c_long
        dll.VBVMR_GetVoicemeeterType.argtypes = [ctypes.POINTER(ctypes.c_long)]
        dll.VBVMR_GetVoicemeeterType.restype = ctypes.c_long
        dll.VBVMR_GetVoicemeeterVersion.argtypes = [ctypes.POINTER(ctypes.c_long)]
        dll.VBVMR_GetVoicemeeterVersion.restype = ctypes.c_long
        dll.VBVMR_IsParametersDirty.restype = ctypes.c_long
        dll.VBVMR_GetParameterFloat.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_float),
        ]
        dll.VBVMR_GetParameterFloat.restype = ctypes.c_long
        dll.VBVMR_SetParameterFloat.argtypes = [ctypes.c_char_p, ctypes.c_float]
        dll.VBVMR_SetParameterFloat.restype = ctypes.c_long
        dll.VBVMR_GetParameterStringA.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        dll.VBVMR_GetParameterStringA.restype = ctypes.c_long
        dll.VBVMR_SetParameterStringA.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        dll.VBVMR_SetParameterStringA.restype = ctypes.c_long

        self._dll = dll
        return dll

    def login(self) -> bool:
        """Connect. Returns True when the mixer is *running*, False when we are
        logged in but it is not (the state `run()` fixes)."""
        with self._lock:
            dll = self._load()
            if self._logged_in:
                return self._engine_up()
            code = int(dll.VBVMR_Login())
            if code not in (_LOGIN_OK, _LOGIN_NOT_RUNNING):
                raise VoicemeeterError("VBVMR_Login failed", code)
            self._logged_in = True
            return code == _LOGIN_OK

    def logout(self) -> None:
        with self._lock:
            if self._dll is not None and self._logged_in:
                try:
                    self._dll.VBVMR_Logout()
                except Exception:  # pragma: no cover - shutdown path
                    pass
            self._logged_in = False

    def _engine_up(self) -> bool:
        """Whether the audio engine is answering.

        Asking for the *type* is the cheapest liveness probe: a non-zero return
        is exactly the "no server" condition, and unlike a parameter read it
        cannot be confused with a legitimately zero value.
        """
        dll = self._load()
        value = ctypes.c_long()
        return int(dll.VBVMR_GetVoicemeeterType(ctypes.byref(value))) == 0

    def run(self, kind_id: int = 3) -> None:
        """Launch Voicemeeter and wait for its engine to answer.

        The wait is the point. `VBVMR_RunVoicemeeter` returns as soon as the
        process is *spawned*, and every parameter written in the second or two
        before the engine is up is silently dropped — which looks like a routing
        change that didn't apply.
        """
        with self._lock:
            dll = self._load()
            code = int(dll.VBVMR_RunVoicemeeter(ctypes.c_long(kind_id)))
            if code != 0:
                raise VoicemeeterError("VBVMR_RunVoicemeeter failed", code)
            deadline = time.monotonic() + _LAUNCH_TIMEOUT
            while time.monotonic() < deadline:
                if self._engine_up():
                    # The first dirty-poll after a connection always reports
                    # dirty and returns stale values with it; burn it here so
                    # the first real read is trustworthy.
                    self.is_dirty()
                    return
                time.sleep(0.25)
            raise VoicemeeterError("Voicemeeter did not start within the timeout")

    # -- parameters --------------------------------------------------------

    def is_dirty(self) -> bool:
        """Whether parameters changed since the last poll — how we notice the
        user moving a fader in Voicemeeter's own window rather than in ours."""
        with self._lock:
            dll = self._load()
            return int(dll.VBVMR_IsParametersDirty()) == 1

    def get_float(self, name: str) -> float:
        with self._lock:
            dll = self._load()
            out = ctypes.c_float()
            code = int(
                dll.VBVMR_GetParameterFloat(name.encode("ascii"), ctypes.byref(out))
            )
            if code != 0:
                raise VoicemeeterError(f"read {name}", code)
            return float(out.value)

    def set_float(self, name: str, value: float) -> None:
        with self._lock:
            dll = self._load()
            code = int(
                dll.VBVMR_SetParameterFloat(name.encode("ascii"), ctypes.c_float(value))
            )
            if code != 0:
                raise VoicemeeterError(f"write {name}", code)

    def get_str(self, name: str) -> str:
        with self._lock:
            dll = self._load()
            buf = ctypes.create_string_buffer(_STR_BUF)
            code = int(dll.VBVMR_GetParameterStringA(name.encode("ascii"), buf))
            if code != 0:
                raise VoicemeeterError(f"read {name}", code)
            return buf.value.decode("utf-8", errors="replace")

    def set_str(self, name: str, value: str) -> None:
        with self._lock:
            dll = self._load()
            code = int(
                dll.VBVMR_SetParameterStringA(
                    name.encode("ascii"), value.encode("utf-8")
                )
            )
            if code != 0:
                raise VoicemeeterError(f"write {name}", code)

    def kind(self) -> int:
        with self._lock:
            dll = self._load()
            out = ctypes.c_long()
            code = int(dll.VBVMR_GetVoicemeeterType(ctypes.byref(out)))
            if code != 0:
                raise VoicemeeterError("VBVMR_GetVoicemeeterType failed", code)
            return int(out.value)

    def version(self) -> str:
        with self._lock:
            dll = self._load()
            out = ctypes.c_long()
            if int(dll.VBVMR_GetVoicemeeterVersion(ctypes.byref(out))) != 0:
                return "unknown"
            raw = int(out.value)
            # Packed one byte per component, most significant first.
            return ".".join(str((raw >> shift) & 0xFF) for shift in (24, 16, 8, 0))


_client = _Client()


def shutdown() -> None:
    """Log out. Called from the app lifespan — an unbalanced login leaves the
    DLL believing a dead process still holds the client slot."""
    _client.logout()


def is_installed() -> bool:
    """Whether the Remote API DLL is present. False on every non-Windows box."""
    return dll_path() is not None


def is_running() -> bool:
    """Whether the mixer's audio engine is answering right now."""
    if not is_installed():
        return False
    try:
        return _client.login()
    except VoicemeeterError:
        return False


def launch(kind_id: int | None = None) -> None:
    """Start Voicemeeter if it is not already up.

    Defaults to the *installed* kind rather than a hardcoded one: launching
    Banana on a machine set up around Potato would present a five-strip mixer
    whose saved routing is not the user's.
    """
    if kind_id is None:
        kind_id = _installed_kind_id()
    _client.run(kind_id)


def _installed_kind_id() -> int:
    """Which Voicemeeter to launch, inferred from the executables on disk.

    All three ship together, so this is a preference order (biggest first), not
    a detection: if the user has Potato they meant Potato. Falls back to Banana,
    the middle version, when the directory cannot be read.

    The executable names do not match the marketing names, and getting them
    backwards launches the wrong mixer against the user's saved routing:
    `voicemeeterpro` is **Banana**, not Potato — Potato is `voicemeeter8`. Each
    also ships an `_x64` sibling, so both spellings count as present.
    """
    directory = install_dir()
    if directory is not None:
        for stem, kind_id in (
            ("voicemeeter8", 3),
            ("voicemeeterpro", 2),
            ("voicemeeter", 1),
        ):
            if (directory / f"{stem}.exe").is_file() or (
                directory / f"{stem}_x64.exe"
            ).is_file():
                return kind_id
    return 2


def _strip_is_virtual(index: int, kind_id: int) -> bool:
    """Virtual strips come after the physical ones — `Strip[3]` and `Strip[4]`
    on Banana. This is positional in the API, with nothing in the parameter name
    to say which is which."""
    _, _, _, physical, _ = KINDS.get(kind_id, KINDS[2])
    return index >= physical


def read_state() -> MixerState:
    """Read the whole mixer: both axes and every matrix cell.

    One pass, no caching. The matrix is at most 8×8 = 64 float reads plus 16
    labels, which is microseconds through an in-process DLL — cheap enough that
    a stale-cache bug is not worth the trade.
    """
    if not _client.login():
        raise VoicemeeterError("Voicemeeter is installed but not running")

    kind_id = _client.kind()
    name, strip_count, bus_count, physical_strips, _ = KINDS.get(kind_id, KINDS[2])
    names = bus_names(kind_id)

    strips: list[Strip] = []
    for i in range(strip_count):
        sends = {}
        for bus in names:
            try:
                sends[bus] = _client.get_float(f"Strip[{i}].{bus}") >= 0.5
            except VoicemeeterError:
                # A cell the running version does not have. Absent, not off:
                # recording it as False would let a UI offer a switch that
                # silently does nothing.
                continue
        strips.append(
            Strip(
                index=i,
                name=f"Strip {i + 1}",
                label=_safe_label(f"Strip[{i}].Label")
                or ("Virtual" if i >= physical_strips else f"Input {i + 1}"),
                is_virtual=_strip_is_virtual(i, kind_id),
                gain=_safe_float(f"Strip[{i}].Gain"),
                muted=_safe_float(f"Strip[{i}].Mute") >= 0.5,
                sends=sends,
            )
        )

    buses: list[Bus] = []
    for i in range(bus_count):
        bus_name = names[i] if i < len(names) else f"Bus{i}"
        buses.append(
            Bus(
                index=i,
                name=bus_name,
                label=_safe_label(f"Bus[{i}].Label") or bus_name,
                is_virtual=bus_name.startswith("B"),
                gain=_safe_float(f"Bus[{i}].Gain"),
                muted=_safe_float(f"Bus[{i}].Mute") >= 0.5,
            )
        )

    return MixerState(
        kind=name,
        kind_id=kind_id,
        version=_client.version(),
        strips=strips,
        buses=buses,
    )


def _safe_float(param: str) -> float:
    try:
        return _client.get_float(param)
    except VoicemeeterError:
        return 0.0


def _safe_label(param: str) -> str:
    try:
        return _client.get_str(param).strip()
    except VoicemeeterError:
        return ""


def set_send(strip: int, bus: str, enabled: bool) -> None:
    """Flip one matrix cell — the whole point of the adapter.

    `bus` is validated against the *running* mixer rather than a fixed list: on
    Banana, `Strip[0].A4` is not an error the API reports usefully, it is a write
    that does nothing.
    """
    if not _client.login():
        raise VoicemeeterError("Voicemeeter is installed but not running")
    valid = bus_names(_client.kind())
    if bus not in valid:
        raise VoicemeeterError(
            f"unknown bus {bus!r}; this mixer has {', '.join(valid)}"
        )
    _client.set_float(f"Strip[{strip}].{bus}", 1.0 if enabled else 0.0)


def set_strip_gain(strip: int, gain_db: float) -> None:
    """Set a strip fader in dB. Clamped to the API's own -60..+12 range, because
    out-of-range writes are rejected wholesale and the fader simply doesn't
    move."""
    if not _client.login():
        raise VoicemeeterError("Voicemeeter is installed but not running")
    _client.set_float(f"Strip[{strip}].Gain", max(-60.0, min(12.0, gain_db)))


def set_strip_mute(strip: int, muted: bool) -> None:
    if not _client.login():
        raise VoicemeeterError("Voicemeeter is installed but not running")
    _client.set_float(f"Strip[{strip}].Mute", 1.0 if muted else 0.0)


def set_bus_gain(bus: int, gain_db: float) -> None:
    if not _client.login():
        raise VoicemeeterError("Voicemeeter is installed but not running")
    _client.set_float(f"Bus[{bus}].Gain", max(-60.0, min(12.0, gain_db)))


def set_bus_mute(bus: int, muted: bool) -> None:
    if not _client.login():
        raise VoicemeeterError("Voicemeeter is installed but not running")
    _client.set_float(f"Bus[{bus}].Mute", 1.0 if muted else 0.0)


def is_dirty() -> bool:
    """Poll for changes made outside the dashboard. Never raises: a poll that
    fails must not take down the broadcast loop that calls it."""
    try:
        if not _client.login():
            return False
        return _client.is_dirty()
    except VoicemeeterError:
        return False
