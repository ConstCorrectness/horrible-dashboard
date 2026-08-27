"""The public viewer page's script.

This page is a string of JavaScript inside a Python module, served to strangers
with no build step and therefore no compiler, no linter and no type checker
between writing it and someone watching a stream through it. Every bug it has
produced so far reached a real viewer before anyone noticed, and each looked
identical from the outside: a black rectangle.

So the checks here are deliberately structural rather than behavioural. They do
not prove the page works -- only a browser can do that -- they prove the specific
shapes that have already failed cannot come back:

- it parses at all (`node --check`);
- `connect()` never touches the shared `pc` after taking its own reference;
- an attempt can tell it has been superseded;
- `setRemoteDescription` cannot reject into the void.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from backend.share_relay import viewer


def _script() -> str:
    """The page's own script, as the browser would receive it."""
    html = viewer.render(
        token="tok", title="Standup", found=True, needs_passphrase=False, live=False
    )
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert blocks, "the viewer page served no script at all"
    return blocks[-1]


def _connect_body(script: str) -> str:
    """Everything from `connect()` up to the next top-level function."""
    start = script.index("async function connect()")
    rest = script[start:]
    end = rest.index("\nretryBtn.addEventListener")
    return rest[:end]


def test_the_page_script_parses() -> None:
    """The only compiler this file ever gets.

    Skipped rather than failed without node: a missing toolchain is not evidence
    of a syntax error, and reporting it as one is the same "could not ask told as
    an answer" mistake the relay status route exists to avoid.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH, so the script cannot be parsed here")

    proc = subprocess.run(
        [node, "--check", "-"],
        input=_script(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"viewer script is not valid JavaScript:\n{proc.stderr}"
    )


def test_connect_never_touches_the_shared_pc_after_taking_its_own() -> None:
    """The `InvalidStateError: Called in wrong state: stable` bug, pinned.

    `connect()` awaits five times, and each await is a window in which it can be
    entered again. While it read and wrote the module-level `pc` throughout, a
    second attempt replaced `pc` mid-flight, the first applied ITS answer to the
    second's connection, and the second then found the thing already stable and
    threw. The attempt died there and the viewer got a black rectangle.

    The fix is that every line after the constructor uses the attempt's own
    `self`, so this asserts exactly that and nothing subtler.
    """
    body = _connect_body(_script())
    after = body[body.index("const self = new RTCPeerConnection") :]

    # `pc = self` is the one legitimate mention: it exists purely so the NEXT
    # attempt can close this one.
    offenders = [
        line.strip()
        for line in after.splitlines()
        if re.search(r"(?<![\w.])pc\.", line)
        or re.search(r"(?<![\w.])pc\b(?! = self)", line)
    ]
    offenders = [line for line in offenders if not line.startswith("//")]
    assert offenders == [], (
        "connect() reaches for the shared `pc` after taking its own reference; "
        "a concurrent attempt will hijack this one:\n  " + "\n  ".join(offenders)
    )


def test_an_attempt_can_tell_it_has_been_superseded() -> None:
    body = _connect_body(_script())
    assert "++generation" in body, "no generation is taken, so no attempt can be stale"
    # Every await is a hand-off point, so each one needs a check after it.
    assert body.count("stale()") >= 6, (
        "too few staleness checks for the number of awaits in connect()"
    )


def test_set_remote_description_cannot_reject_into_the_void() -> None:
    """An unhandled rejection here is invisible except as a black screen."""
    body = _connect_body(_script())
    # The CALL, not the comment above that explains why it is guarded.
    call = body.index("await self.setRemoteDescription")
    before = body[:call]
    assert before.rstrip().endswith("try {"), (
        "setRemoteDescription is not inside a try block, so a superseded attempt "
        "reports itself only as an unhandled promise rejection"
    )


def test_the_page_does_not_claim_live_on_the_answer_alone() -> None:
    """An SDP answer means the relay agreed to send, not that a path exists.

    Claiming `live` here is what produced a green chip over a black rectangle --
    indistinguishable, to the viewer, from a host genuinely sharing a black
    screen.
    """
    body = _connect_body(_script())
    after_answer = body[body.index("await self.setRemoteDescription") :]
    assert "setStatus('live'" not in after_answer, (
        "the page promotes itself to 'live' straight off the SDP answer"
    )
    assert "connectionState === 'connected'" in _script()


def test_the_retry_is_cancellable() -> None:
    """Uncancelled, each entry into connect() forks an immortal 4s loop."""
    body = _connect_body(_script())
    assert "retryTimer = setTimeout(connect" in body
    assert "clearTimeout(retryTimer)" in body


def test_a_missing_link_never_reaches_the_network() -> None:
    """A revoked token should render as an explanation, not an offer."""
    html = viewer.render(
        token="tok", title="", found=False, needs_passphrase=False, live=False
    )
    assert '"found": false' in html


def test_the_title_is_escaped() -> None:
    """The one host-supplied value that reaches the document."""
    html = viewer.render(
        token="tok",
        title="<img src=x onerror=alert(1)>",
        found=True,
        needs_passphrase=False,
        live=False,
    )
    assert "<img src=x" not in html
    assert "&lt;img" in html


# --- the page must fit the window, always ------------------------------------


def test_the_page_is_exactly_one_viewport_tall() -> None:
    """`min-height: 100vh` let the page grow past the window.

    Both columns inside can grow without bound -- the chat log and the
    diagnostics panel -- and with `min-height` the first long conversation
    pushed the stage off the bottom. A shared screen you have to scroll to is a
    shared screen you cannot watch.
    """
    html = viewer.render(
        token="tok", title="x", found=True, needs_passphrase=False, live=False
    )
    body_rule = re.search(r"\nbody \{(.*?)\}", html, re.S)
    assert body_rule, "no body rule in the page style"
    css = body_rule.group(1)
    assert "min-height: 100vh" not in css, (
        "body uses min-height, so any tall child grows the page past the window"
    )
    assert "height: 100vh" in css and "height: 100dvh" in css, (
        "body must be pinned to the viewport, with dvh for mobile URL bars"
    )
    assert "overflow: hidden" in css


def test_every_scrolling_column_can_actually_shrink() -> None:
    """A flex item defaults to `min-height: auto` and refuses to shrink.

    That is the mechanism by which a child's content, not the child's own
    styling, breaks a fixed layout -- so the containers that hold growing
    content have to say otherwise explicitly.
    """
    html = viewer.render(
        token="tok", title="x", found=True, needs_passphrase=False, live=False
    )
    for selector in ("main {", ".stage {", "aside {"):
        rule = re.search(re.escape(selector) + r"(.*?)\}", html, re.S)
        assert rule, f"no rule for {selector}"
        assert "min-height: 0" in rule.group(1), (
            f"{selector.strip(' {')} cannot shrink below its content, so a tall "
            "child will push the page past the viewport"
        )


# --- progress and diagnostics ------------------------------------------------


def test_the_viewer_is_shown_connection_progress() -> None:
    """Someone waiting should see which of the six steps is being waited on."""
    script = _script()
    assert "PHASES" in script
    for phase in ("offer", "gather", "relay", "negotiate", "path", "live"):
        assert f"'{phase}'" in script, f"phase {phase} is not reported"
    html = viewer.render(
        token="tok", title="x", found=True, needs_passphrase=False, live=False
    )
    assert "role='progressbar'" in html
    assert "aria-valuenow" in html


def test_waiting_for_the_host_does_not_pretend_to_be_progress() -> None:
    """A bar creeping toward 100% would be a lie about something not started.

    The host may never start. So that state sweeps and counts down instead of
    advancing, and the countdown is what stops the page looking abandoned.
    """
    script = _script()
    assert "setWaiting(" in script
    assert "countdown(" in script
    assert "retrying in " in script


def test_the_page_keeps_a_diagnostic_log() -> None:
    """Diagnosing this used to mean reading the relay's Fly logs.

    The relay sees requests. It cannot see ICE failing, a track arriving, or
    autoplay being blocked -- which is where every real failure has been.
    """
    script = _script()
    assert "diagLine(" in script
    assert "DIAG_MAX" in script, "the log must be bounded; viewers watch for hours"
    assert "getStats" in script, "no quality sampling, so 'laggy' stays an adjective"
    for event in ("oniceconnectionstatechange", "onicegatheringstatechange", "ontrack"):
        assert event in script, f"{event} is not logged"


def test_the_diagnostic_log_does_not_leak_the_viewer_s_addresses() -> None:
    """The panel exists to be copied to somebody else.

    A full ICE candidate line carries the viewer's own IP addresses, so only the
    type and protocol are recorded.
    """
    script = _script()
    candidate_log = re.search(r"onicecandidate[^}]*?diagLine\((.*?)\);", script, re.S)
    assert candidate_log, "candidates are not logged at all"
    logged = candidate_log.group(1)
    assert "e.candidate.type" in logged
    assert "e.candidate.candidate" not in logged, (
        "the raw candidate line carries the viewer's addresses"
    )


def test_closing_the_tab_releases_the_relay_slot() -> None:
    """A slot is a full-resolution encoder on a memory-bound machine.

    Without this the relay holds it until ICE consent expires ~30s later.
    """
    script = _script()
    assert "pagehide" in script
    hook = script[script.index("pagehide") :]
    assert "pc.close()" in hook[:400]
