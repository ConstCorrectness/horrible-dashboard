"""Audio: default input, default output, and a routing matrix over both.

The dashboard plays sound from half a dozen places — karaoke, the agent's voice,
game effects, peer voice comms — and until now every one of them opened its own
`AudioContext` and connected to whatever the OS called "default". There was no
way to say "the karaoke video goes to the living-room speakers, my microphone
does not" because nothing in the app modelled *which output* a sound goes to.

This module is the VoiceMeeter answer: sources are **strips**, outputs are
**buses**, and the routing is a matrix where any strip can feed any number of
buses at once. That last part is the whole feature — playing a video into a
virtual microphone *while still hearing it yourself* is one strip with two sends,
and it is unexpressible in a model with a single "output device" setting.

The graph itself lives in the browser (only Web Audio can move the samples); this
backend owns the parts a browser cannot: durable routing, the honest per-platform
answer about virtual devices, and — on Windows — control of Voicemeeter's own
matrix, which is the only way to route audio the dashboard does not produce.

See docs/modules/audio.mdx.
"""

from backend.modules.audio.agent_tools import register_agent_tools
from backend.modules.audio.events import CHANNEL, publish_host, publish_mixer
from backend.modules.audio.providers import (
    ProviderStatus,
    VirtualAudioProvider,
    VirtualDevice,
    get_provider,
)
from backend.modules.audio.routes import router
from backend.modules.audio.store import init_audio_db, load_state, save_state
from backend.modules.audio.voicemeeter import shutdown as shutdown_voicemeeter

__all__ = [
    "CHANNEL",
    "ProviderStatus",
    "VirtualAudioProvider",
    "VirtualDevice",
    "get_provider",
    "init_audio_db",
    "load_state",
    "publish_host",
    "publish_mixer",
    "register_agent_tools",
    "router",
    "save_state",
    "shutdown_voicemeeter",
]
