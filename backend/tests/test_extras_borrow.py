"""The extras probe (`backend/extras.py`) and borrow routing (`network/borrow.py`).

Two properties carry the weight here.

**Three states, not two.** An extra that is installed but will not load is a
different fact from one that was never installed, and the second-most-likely bug
in this area is collapsing them — which sends someone to reinstall a package
already sitting on disk.

**"Could not ask" never routes to a peer.** Borrowing on an uncertain local answer
would paper over a broken local install by silently shipping the user's audio to
another machine.
"""

import pytest

from backend import extras
from backend.modules.network import borrow


@pytest.fixture(autouse=True)
def _clean():
    extras.reset_cache()
    yield
    extras.reset_cache()


def _fake_import(monkeypatch, behaviour):
    """Replace importlib for the probe. `behaviour` maps module name -> None (ok)
    or an exception instance to raise."""

    def fake(name):
        outcome = behaviour.get(name)
        if isinstance(outcome, BaseException):
            raise outcome
        return object()

    monkeypatch.setattr(extras.importlib, "import_module", fake)


# ---- the three states ----------------------------------------------------------


def test_installed(monkeypatch):
    _fake_import(monkeypatch, {"onnxruntime": None, "tokenizers": None})
    verdict = extras.probe("clip")
    assert verdict.available is True
    assert verdict.certain is True
    assert verdict.state == "installed"


def test_import_error_is_absence(monkeypatch):
    _fake_import(monkeypatch, {"onnxruntime": ImportError("no module")})
    verdict = extras.probe("clip")
    assert verdict.available is False
    assert verdict.certain is True
    assert verdict.state == "absent"
    assert verdict.install == "uv sync --extra clip"


def test_a_broken_native_load_is_unknown_not_absent(monkeypatch):
    """The distinction this module exists for. A DLL that will not load means the
    package IS installed — reporting 'not installed' sends the user to reinstall
    something already on disk."""
    _fake_import(monkeypatch, {"onnxruntime": OSError("DLL load failed")})
    verdict = extras.probe("clip")
    assert verdict.available is False
    assert verdict.certain is False
    assert verdict.state == "unknown"
    assert "would not load" in verdict.reason


def test_an_unknown_extra_name_is_our_bug_not_the_users(monkeypatch):
    """`certain=False` so nothing downstream renders it as 'the user has not
    installed this'."""
    verdict = extras.probe("no-such-extra")
    assert verdict.certain is False
    assert "declared" in verdict.reason


def test_a_probe_that_raises_does_not_propagate(monkeypatch):
    def boom(name):
        raise RuntimeError("probe itself is broken")

    monkeypatch.setattr(extras.importlib, "import_module", boom)
    verdict = extras.probe("clip")
    assert verdict.available is False
    assert verdict.certain is False


def test_binary_extras_are_probed_on_path(monkeypatch):
    monkeypatch.setattr(extras.shutil, "which", lambda name: None)
    verdict = extras.probe("ffmpeg")
    assert verdict.available is False
    assert verdict.certain is True
    assert "PATH" in verdict.reason

    extras.reset_cache()
    monkeypatch.setattr(extras.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert extras.probe("ffmpeg").available is True


def test_results_are_cached(monkeypatch):
    calls = []

    def counting(name):
        calls.append(name)
        return object()

    monkeypatch.setattr(extras.importlib, "import_module", counting)
    extras.probe("llamacpp")
    extras.probe("llamacpp")
    assert len(calls) == 1
    extras.probe("llamacpp", refresh=True)
    assert len(calls) == 2


# ---- the migrated guards keep their old shape -----------------------------------


def test_clip_installed_still_returns_a_bare_bool(monkeypatch):
    """Its callers want yes/no. The three-state answer is available separately —
    collapsing here is deliberate, and an unloadable runtime must answer False."""
    from backend.modules.library import clip

    _fake_import(monkeypatch, {"onnxruntime": OSError("broken")})
    assert clip.clip_installed() is False


def test_trace_runner_reports_the_uncertain_case_differently(monkeypatch):
    """It kept its `(bool, reason)` contract, but 'could not determine' no longer
    reads as 'not installed'."""
    from backend.modules.llamacpp import trace_runner

    _fake_import(monkeypatch, {"llama_cpp": ImportError("nope")})
    ok, reason = trace_runner.available()
    assert ok is False
    assert "Install it with" in reason

    extras.reset_cache()
    _fake_import(monkeypatch, {"llama_cpp": OSError("bad DLL")})
    ok, reason = trace_runner.available()
    assert ok is False
    assert "could not determine" in reason


# ---- routing --------------------------------------------------------------------


def _peer(node_id, installed, trusted=True, status="connected"):
    from backend.modules.network.models import PeerCapability, PeerInfo

    return PeerInfo(
        node_id=node_id,
        node_name=node_id,
        public_key="k",
        transport="direct",
        status=status,
        trusted=trusted,
        capabilities=["extras"],
        caps=[PeerCapability(id="extras", attrs={"installed": installed})],
    )


def _patch_peers(monkeypatch, peers):
    from backend.modules.network import hub as hub_mod

    monkeypatch.setattr(hub_mod.peer_hub, "list_peers", lambda: peers)


def test_local_wins(monkeypatch):
    _fake_import(monkeypatch, {"torch": None, "edge_tts": None})
    _patch_peers(monkeypatch, [_peer("friend", ["voice"])])
    assert borrow.route("voice").where == "local"


def test_a_peer_is_used_when_the_extra_is_certainly_absent(monkeypatch):
    _fake_import(monkeypatch, {"torch": ImportError("no torch")})
    _patch_peers(monkeypatch, [_peer("friend", ["voice"])])
    decision = borrow.route("voice")
    assert decision.where == "peer"
    assert decision.node_id == "friend"


def test_an_uncertain_local_answer_never_routes_to_a_peer(monkeypatch):
    """A broken local install is a problem the user can fix. Silently shipping
    their audio to a friend's machine instead hides it."""
    _fake_import(monkeypatch, {"torch": OSError("torch is present but broken")})
    _patch_peers(monkeypatch, [_peer("friend", ["voice"])])
    decision = borrow.route("voice")
    assert decision.where == "unavailable"
    assert decision.node_id is None


def test_an_untrusted_peer_is_not_a_candidate(monkeypatch):
    _fake_import(monkeypatch, {"torch": ImportError("no torch")})
    _patch_peers(monkeypatch, [_peer("stranger", ["voice"], trusted=False)])
    assert borrow.route("voice").where == "unavailable"


def test_a_disconnected_peer_is_not_a_candidate(monkeypatch):
    _fake_import(monkeypatch, {"torch": ImportError("no torch")})
    _patch_peers(monkeypatch, [_peer("friend", ["voice"], status="disconnected")])
    assert borrow.route("voice").where == "unavailable"


def test_a_peer_without_the_extra_is_not_a_candidate(monkeypatch):
    _fake_import(monkeypatch, {"torch": ImportError("no torch")})
    _patch_peers(monkeypatch, [_peer("friend", ["clip"])])
    decision = borrow.route("voice")
    assert decision.where == "unavailable"
    assert "no connected friend" in decision.reason


def test_the_install_hint_survives_the_borrow_attempt(monkeypatch):
    """The fallback is the hint the feature always gave, not a new dead end."""
    _fake_import(monkeypatch, {"torch": ImportError("no torch")})
    _patch_peers(monkeypatch, [])
    decision = borrow.route("voice")
    assert decision.install == "uv sync --extra voice"


def test_allow_peer_false_skips_the_fabric_entirely(monkeypatch):
    _fake_import(monkeypatch, {"torch": ImportError("no torch")})
    _patch_peers(monkeypatch, [_peer("friend", ["voice"])])
    assert borrow.route("voice", allow_peer=False).where == "unavailable"


# ---- advertisement ---------------------------------------------------------------


def test_capability_lists_only_installed_extras(monkeypatch):
    _fake_import(
        monkeypatch,
        {
            "torch": None,
            "edge_tts": None,
            "onnxruntime": ImportError("no"),
            "llama_cpp": ImportError("no"),
            "playwright": ImportError("no"),
        },
    )
    cap = borrow.capability()
    assert cap is not None
    assert cap.attrs["installed"] == ["voice"]


def test_capability_withdraws_when_nothing_is_installed(monkeypatch):
    """Advertising an absent extra makes a peer's UI offer something every request
    against it would refuse."""
    _fake_import(
        monkeypatch,
        {
            name: ImportError("no")
            for name in (
                "torch",
                "edge_tts",
                "onnxruntime",
                "tokenizers",
                "llama_cpp",
                "playwright",
            )
        },
    )
    assert borrow.capability() is None


def test_an_uncertain_extra_is_not_advertised(monkeypatch):
    """Worse than advertising an absent one: it would send a friend's work to a
    machine whose own install is broken."""
    _fake_import(
        monkeypatch,
        {
            "torch": OSError("present but broken"),
            "onnxruntime": ImportError("no"),
            "llama_cpp": ImportError("no"),
            "playwright": ImportError("no"),
        },
    )
    assert borrow.capability() is None
