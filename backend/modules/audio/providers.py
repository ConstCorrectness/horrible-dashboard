"""Virtual audio devices, per platform, behind one protocol.

**The thing no web app can do.** The mixer in `packages/core/src/modules/audio/`
routes the dashboard's own audio to any output device the OS offers, and that is
enough for "which speakers do I hear this on". It is *not* enough for "another
application should receive this as its microphone" — that needs a device that
does not exist until some kernel-level driver creates it. This module is the one
place the backend deals with those devices: it finds them, reports honestly when
it cannot, and on the one platform where creating them is free, creates them.

**The platforms differ in kind, not degree**, so the protocol has two separate
capability flags rather than one:

- **Linux** (`can_create`): PipeWire makes a virtual sink+source on demand with
  one `pactl load-module`. No third-party install, no user setup — the dashboard
  can simply produce the cable it needs. This is the best-supported platform,
  which surprises people.
- **Windows** (`can_control`): the device has to be installed (Voicemeeter,
  VB-CABLE, or one of several vendor drivers), but Voicemeeter exposes a remote
  API, so once present the dashboard can rearrange the machine's *whole* mixer —
  including audio the dashboard does not own. See `voicemeeter.py`.
- **macOS** (neither): BlackHole is a free driver that gives us the device, but
  there is no scripting surface, and creating an aggregate device requires a
  native CoreAudio helper this module deliberately does not pretend to have. We
  detect, report, and point at the install.

**Nothing is bundled.** VB-CABLE is donationware with restricted redistribution
and BlackHole is a kernel-adjacent driver requiring its own installer; both get
the treatment SearXNG and the AssaultCube content get — supported, detected,
linked, never shipped.

**Three states, never two** (the `hardware.probe` rule). `installed=False` with
`certain=True` means we looked and it is not there. `certain=False` means we
could not ask — no `pactl` on PATH, a registry we cannot read, a sandbox with no
subprocess. Rendering the second as the first tells a user with a working
Voicemeeter that they do not have one.
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from backend.modules.audio import voicemeeter

logger = logging.getLogger(__name__)

#: Every probe subprocess gets this long. A hung `pactl` must not hold a request.
_TIMEOUT = 5.0

#: Name we give sinks we create ourselves, so `cleanup` can tell ours from the
#: user's. Matching on a name prefix is the only handle PulseAudio's module API
#: gives us across processes — the module id is not stable across a restart.
_OWNED_PREFIX = "horrible_"


@dataclass(frozen=True)
class VirtualDevice:
    """One virtual device the OS is offering.

    `kind` is from the *application's* point of view, which is the confusing part
    of every virtual-cable setup: the thing you send audio *to* is a sink (an
    output device), and the thing another app then records *from* is a source (an
    input device). A cable is one of each, wired together — so `duplex` means the
    provider exposes the pair under one name.
    """

    id: str
    name: str
    kind: str  # "sink" | "source" | "duplex"
    owned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "owned": self.owned,
        }


@dataclass
class ProviderStatus:
    """What this machine can do about virtual audio, and how sure we are."""

    platform: str
    provider: str | None
    installed: bool
    running: bool
    #: False means *we could not ask*, and is never the same fact as
    #: `installed=False`. See the module docstring.
    certain: bool = True
    can_create: bool = False
    can_control: bool = False
    note: str = ""
    install_name: str = ""
    install_url: str | None = None
    devices: list[VirtualDevice] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "provider": self.provider,
            "installed": self.installed,
            "running": self.running,
            "certain": self.certain,
            "canCreate": self.can_create,
            "canControl": self.can_control,
            "note": self.note,
            "installName": self.install_name,
            "installUrl": self.install_url,
            "devices": [d.to_dict() for d in self.devices],
        }


class VirtualAudioProvider(Protocol):
    """What every platform adapter implements.

    Deliberately small. Anything richer would only be implementable on one
    platform, and a protocol whose methods raise `NotImplementedError` on two of
    three targets is a protocol that lies about what the app can do.
    """

    name: str

    def status(self) -> ProviderStatus: ...

    def create(self, label: str) -> VirtualDevice:
        """Create a virtual cable. Only meaningful when `can_create`."""
        ...

    def destroy(self, device_id: str) -> None:
        """Remove a cable we created. Never removes one we did not."""
        ...


def _run(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a probe tool. None means *we could not ask* — the tool is missing, it
    timed out, or the platform refused to spawn it.

    Sync on purpose: these are short-lived, and asyncio subprocess spawning is
    broken under `uvicorn --reload` on Windows. Callers use `asyncio.to_thread`,
    the same shape as the hardware probe and the LSP/PTY spawns.
    """
    if shutil.which(args[0]) is None:
        return None
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=_TIMEOUT)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("audio: %s failed: %s", args[0], exc)
        return None


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


class WindowsProvider:
    """Voicemeeter and VB-CABLE.

    The install is the user's job; what we add is *control*. When Voicemeeter is
    running we can read and write its whole matrix, which is the only path in the
    app to routing audio the dashboard does not produce.
    """

    name = "windows"

    def status(self) -> ProviderStatus:
        installed = voicemeeter.is_installed()
        running = voicemeeter.is_running() if installed else False
        devices = self._devices()

        if installed:
            note = (
                "Voicemeeter is running; its full routing matrix is available."
                if running
                else "Voicemeeter is installed but not running. Start it to route audio from other applications."
            )
        elif devices:
            # A virtual cable from some other vendor. Routing *into* it works
            # through the mixer; there is just no matrix to drive.
            note = "A virtual audio device is available. Install Voicemeeter to also route other applications' audio."
        else:
            note = "No virtual audio device found. Dashboard audio can still be routed between real devices."

        return ProviderStatus(
            platform="windows",
            provider="voicemeeter" if installed else None,
            installed=installed,
            running=running,
            certain=True,
            can_create=False,
            can_control=running,
            note=note,
            install_name="VB-Audio Voicemeeter",
            install_url="https://vb-audio.com/Voicemeeter/",
            devices=devices,
        )

    def _devices(self) -> list[VirtualDevice]:
        """Virtual devices Windows is exposing.

        Matched by name against the known virtual drivers. A name match is crude,
        but Windows offers no "is this device virtual" flag at all, and the
        frontend gets the authoritative list from `enumerateDevices()` anyway —
        this exists so the *settings page* can say "you have a cable" before the
        user has granted microphone permission, which is what gates the browser's
        own device labels.
        """
        result = _run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_SoundDevice | Select-Object -ExpandProperty Name",
            ]
        )
        if result is None or result.returncode != 0:
            return []
        known = ("voicemeeter", "vb-audio", "cable", "virtual")
        out: list[VirtualDevice] = []
        for line in result.stdout.splitlines():
            entry = line.strip()
            if entry and any(token in entry.lower() for token in known):
                out.append(VirtualDevice(id=entry, name=entry, kind="duplex"))
        return out

    def create(self, label: str) -> VirtualDevice:
        raise NotImplementedError(
            "Creating a virtual audio device on Windows needs a driver install; "
            "install VB-CABLE or Voicemeeter instead."
        )

    def destroy(self, device_id: str) -> None:
        raise NotImplementedError(
            "Windows virtual devices are removed by uninstalling their driver."
        )


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------


class LinuxProvider:
    """PipeWire (or PulseAudio) — the one platform where we make the cable.

    `module-null-sink` with `media.class=Audio/Source/Virtual` produces a device
    that is a sink to whoever writes it and a source to whoever records it, which
    is exactly a virtual cable and costs one command. No install, no driver, no
    third party.
    """

    name = "linux"

    def status(self) -> ProviderStatus:
        result = _run(["pactl", "info"])
        if result is None:
            return ProviderStatus(
                platform="linux",
                provider=None,
                installed=False,
                running=False,
                # The distinction this whole module is built around: no `pactl`
                # on PATH is not evidence there is no sound server.
                certain=False,
                note="Could not run `pactl`, so PipeWire/PulseAudio could not be queried. Install pulseaudio-utils to enable virtual devices.",
                install_name="PipeWire",
                install_url="https://pipewire.org/",
            )
        if result.returncode != 0:
            return ProviderStatus(
                platform="linux",
                provider=None,
                installed=False,
                running=False,
                certain=True,
                note="No PulseAudio/PipeWire server is running.",
                install_name="PipeWire",
                install_url="https://pipewire.org/",
            )

        server = ""
        for line in result.stdout.splitlines():
            if line.startswith("Server Name:"):
                server = line.split(":", 1)[1].strip()
                break
        is_pipewire = "pipewire" in server.lower()
        return ProviderStatus(
            platform="linux",
            provider="pipewire" if is_pipewire else "pulseaudio",
            installed=True,
            running=True,
            certain=True,
            can_create=True,
            # No matrix to drive: routing *other* apps means moving their
            # streams, which is a per-stream operation, not a mixer.
            can_control=False,
            note=f"{server or 'Sound server'} is running; virtual devices can be created on demand.",
            install_name="PipeWire",
            install_url="https://pipewire.org/",
            devices=self._devices(),
        )

    def _devices(self) -> list[VirtualDevice]:
        result = _run(["pactl", "-f", "json", "list", "sinks"])
        if result is None or result.returncode != 0:
            return []
        try:
            sinks = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        out: list[VirtualDevice] = []
        for sink in sinks:
            name = str(sink.get("name", ""))
            driver = str(sink.get("driver", "")).lower()
            # A null sink has no hardware behind it — that is what makes it a
            # cable rather than a speaker.
            if "null" in driver or name.startswith(_OWNED_PREFIX):
                out.append(
                    VirtualDevice(
                        id=name,
                        name=str(sink.get("description") or name),
                        kind="duplex",
                        owned=name.startswith(_OWNED_PREFIX),
                    )
                )
        return out

    def create(self, label: str) -> VirtualDevice:
        """Create a virtual cable named after `label`.

        `Audio/Source/Virtual` rather than a plain null sink: the plain form
        gives you a sink plus a `.monitor` source, and monitors are hidden by
        default in most application device pickers — so the cable would work and
        appear not to.
        """
        safe = (
            _OWNED_PREFIX
            + "".join(c if c.isalnum() else "_" for c in label).strip("_").lower()
        )
        result = _run(
            [
                "pactl",
                "load-module",
                "module-null-sink",
                "media.class=Audio/Source/Virtual",
                f"sink_name={safe}",
                f'sink_properties=device.description="{label}"',
            ]
        )
        if result is None or result.returncode != 0:
            detail = (
                result.stderr.strip() if result else "pactl unavailable"
            ) or "unknown error"
            raise RuntimeError(f"could not create virtual device: {detail}")
        return VirtualDevice(id=safe, name=label, kind="duplex", owned=True)

    def destroy(self, device_id: str) -> None:
        """Unload a cable we created.

        Refuses anything without our prefix. `pactl unload-module` takes a *name*
        and would happily unload a module underpinning the user's real sound
        card, so "only ours" is a safety gate, not tidiness.
        """
        if not device_id.startswith(_OWNED_PREFIX):
            raise ValueError(
                f"{device_id!r} was not created by the dashboard; refusing to remove it"
            )
        result = _run(["pactl", "unload-module", device_id])
        if result is None or result.returncode != 0:
            raise RuntimeError(f"could not remove {device_id}")


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------


#: Where CoreAudio loads HAL plug-ins from. BlackHole installs here, and the
#: presence of the bundle is a stronger signal than a device-name match — the
#: device only appears once CoreAudio has been restarted.
_HAL_DIRS = (
    Path("/Library/Audio/Plug-Ins/HAL"),
    Path.home() / "Library/Audio/Plug-Ins/HAL",
)


class MacProvider:
    """BlackHole, detected and reported — not driven.

    There is no remote API. Routing happens entirely in the frontend mixer by
    choosing BlackHole as a bus output; this adapter's whole job is to answer
    "do you have a cable, and if not where do I get one" honestly.

    Hearing the audio *and* sending it needs a Multi-Output Device, which is a
    CoreAudio call we would need a native helper to make. We say so rather than
    silently doing half of it.
    """

    name = "darwin"

    def status(self) -> ProviderStatus:
        bundles = self._bundles()
        devices = self._devices()
        installed = bool(bundles) or bool(devices)

        if bundles and not devices:
            note = "BlackHole is installed but no virtual device is present yet — a restart (or `sudo killall coreaudiod`) may be needed."
        elif installed:
            note = "A virtual audio device is available. To hear audio while also sending it, create a Multi-Output Device in Audio MIDI Setup."
        else:
            note = "No virtual audio device found. Dashboard audio can still be routed between real devices."

        return ProviderStatus(
            platform="darwin",
            provider="blackhole" if installed else None,
            installed=installed,
            running=installed,
            certain=True,
            can_create=False,
            can_control=False,
            note=note,
            install_name="BlackHole",
            install_url="https://existential.audio/blackhole/",
            devices=devices,
        )

    def _bundles(self) -> list[str]:
        found: list[str] = []
        for directory in _HAL_DIRS:
            try:
                if not directory.is_dir():
                    continue
                found += [p.name for p in directory.iterdir() if p.suffix == ".driver"]
            except OSError:
                continue
        return found

    def _devices(self) -> list[VirtualDevice]:
        result = _run(["system_profiler", "-json", "SPAudioDataType"])
        if result is None or result.returncode != 0:
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        known = (
            "blackhole",
            "soundflower",
            "loopback",
            "virtual",
            "aggregate",
            "multi-output",
        )
        out: list[VirtualDevice] = []
        for group in payload.get("SPAudioDataType", []):
            for item in group.get("_items", []):
                label = str(item.get("_name", ""))
                if label and any(token in label.lower() for token in known):
                    out.append(VirtualDevice(id=label, name=label, kind="duplex"))
        return out

    def create(self, label: str) -> VirtualDevice:
        raise NotImplementedError(
            "Creating a virtual audio device on macOS needs a driver install; install BlackHole instead."
        )

    def destroy(self, device_id: str) -> None:
        raise NotImplementedError(
            "macOS virtual devices are removed by uninstalling their driver."
        )


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


class UnknownProvider:
    """The honest fallback for a platform we have no adapter for.

    Reports `certain=False`, because "we have never been taught about this OS" is
    the *could not ask* state, not evidence of anything.
    """

    name = "unknown"

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            platform=platform.system().lower() or "unknown",
            provider=None,
            installed=False,
            running=False,
            certain=False,
            note="No virtual-audio adapter for this platform. Routing between real devices still works.",
        )

    def create(self, label: str) -> VirtualDevice:
        raise NotImplementedError("no virtual-audio adapter for this platform")

    def destroy(self, device_id: str) -> None:
        raise NotImplementedError("no virtual-audio adapter for this platform")


def get_provider() -> VirtualAudioProvider:
    """The adapter for the machine we are on. Cheap; not cached, because a user
    can install BlackHole or start Voicemeeter without restarting the app, and a
    cached "not installed" would outlive the fix."""
    system = platform.system()
    if system == "Windows":
        return WindowsProvider()
    if system == "Linux":
        return LinuxProvider()
    if system == "Darwin":
        return MacProvider()
    return UnknownProvider()
