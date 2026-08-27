"""The public viewer page: one self-contained HTML document.

No CDN, no build step, no framework. This page is served to strangers from a
relay that holds no session state and runs no bundler, and a `<script src>`
pointing at somebody else's origin would be both a dependency and a tracking
vector on a page whose entire promise is "watch this, nothing else".

The title is host-supplied, so it is **escaped**, and it is the only host-supplied
value that reaches the document. Everything else the page shows it learns from
the relay's own JSON.
"""

from __future__ import annotations

import html
import json

from backend.share_relay import ice

_STYLE = """
:root {
  color-scheme: dark;
  --bg: #0e1014;
  --surface: #16181d;
  --border: #262934;
  --text: #e6e8ee;
  --dim: #9aa1b1;
  --accent: #6366f1;
  --ok: #3fb950;
  --warn: #e2c08d;
  --danger: #f85149;
  --mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  --sans: Inter, system-ui, -apple-system, 'Segoe UI', sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text); font-family: var(--sans);
  min-height: 100vh; display: flex; flex-direction: column;
}
header {
  display: flex; align-items: center; gap: 12px; padding: 10px 16px;
  border-bottom: 1px solid var(--border); background: var(--surface);
}
header .title {
  font-size: 12px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
}
header .spacer { flex: 1; }
.chip {
  display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px;
  border-radius: 999px; font-size: 11.5px; font-family: var(--mono);
  background: rgba(255,255,255,0.05); color: var(--dim);
}
.chip .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.chip.live { color: var(--ok); background: rgba(63,185,80,0.12); }
.chip.off  { color: var(--warn); background: rgba(226,192,141,0.12); }
main { flex: 1; display: flex; min-height: 0; }
.stage { flex: 1; display: flex; align-items: center; justify-content: center;
  background: #000; min-width: 0; position: relative; }
video { width: 100%; height: 100%; object-fit: contain; background: #000; }
.overlay {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 14px; text-align: center;
  padding: 24px; background: rgba(14,16,20,0.92);
}
.overlay h1 { font-size: 15px; font-weight: 700; letter-spacing: 0.12em;
  text-transform: uppercase; margin: 0; }
.overlay p { margin: 0; color: var(--dim); font-size: 13px; max-width: 42ch;
  line-height: 1.55; }
.overlay.hidden { display: none; }
input {
  height: 30px; border-radius: 6px; border: 1px solid var(--border);
  background: #0b0d11; color: var(--text); padding: 0 10px; font-family: var(--mono);
  font-size: 12.5px; min-width: 220px;
}
button {
  height: 30px; border-radius: 6px; border: 1px solid transparent;
  background: var(--accent); color: #fff; font-size: 12.5px; font-weight: 600;
  padding: 0 14px; cursor: pointer;
}
button.ghost { background: transparent; border-color: var(--border); color: var(--dim); }
button:disabled { opacity: 0.5; cursor: default; }
aside {
  width: 280px; border-left: 1px solid var(--border); background: var(--surface);
  display: flex; flex-direction: column; min-height: 0;
}
aside .head { padding: 9px 12px; border-bottom: 1px solid var(--border);
  font-size: 11px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
  color: var(--dim); }
#log { flex: 1; overflow-y: auto; padding: 10px 12px; display: flex;
  flex-direction: column; gap: 8px; }
.msg { font-size: 12.5px; line-height: 1.45; word-break: break-word; }
.msg .who { font-family: var(--mono); font-size: 11px; color: var(--accent);
  display: block; }
.msg.sys { color: var(--dim); font-style: italic; }
.compose { display: flex; gap: 6px; padding: 10px 12px; border-top: 1px solid var(--border); }
.compose input { flex: 1; min-width: 0; }
footer { padding: 7px 16px; border-top: 1px solid var(--border);
  font-family: var(--mono); font-size: 11px; color: var(--dim); }
@media (max-width: 760px) {
  main { flex-direction: column; }
  aside { width: auto; border-left: none; border-top: 1px solid var(--border);
    max-height: 42vh; }
}
"""

_SCRIPT = r"""
const CFG = window.__SHARE__;
const $ = (id) => document.getElementById(id);
const video = $('video');
const overlay = $('overlay');
const overlayTitle = $('overlay-title');
const overlayText = $('overlay-text');
const passRow = $('pass-row');
const passInput = $('pass');
const retryBtn = $('retry');
const statusChip = $('status');
const log = $('log');

let pc = null;
let passphrase = '';
let attempts = 0;
// The pending "not started yet" retry, so a second call to connect() cancels it
// rather than racing it. Without this every path into connect() -- the initial
// call, the Retry button, an unlock -- forks its own 4s loop that nothing ever
// stops, and the loops compound: observed at roughly one WHEP per second against
// a relay that was being asked to wait. Worse than the noise, each loop that
// eventually succeeds becomes a REAL viewer session on the relay, and a viewer
// session is a full-resolution encoder. A retry storm is a memory leak on the
// relay wearing a network-noise costume.
let retryTimer = null;
// How long to wait for a path after the relay has answered, before saying so.
const CONNECT_TIMEOUT_MS = 12000;
let connectDeadline = null;
// Bumped by every connect() attempt. An attempt whose generation is no longer
// current has been superseded and must touch neither `pc` nor the UI.
let generation = 0;

function setStatus(text, live) {
  statusChip.className = 'chip ' + (live ? 'live' : 'off');
  statusChip.innerHTML = '<span class="dot"></span>' + text;
}

function showOverlay(title, text, withPass) {
  overlay.classList.remove('hidden');
  overlayTitle.textContent = title;
  overlayText.textContent = text;
  passRow.style.display = withPass ? 'flex' : 'none';
  retryBtn.style.display = withPass ? 'none' : 'inline-block';
}

function hideOverlay() { overlay.classList.add('hidden'); }

function sysLine(text) {
  const el = document.createElement('div');
  el.className = 'msg sys';
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}

// --- playback (WHEP) -------------------------------------------------------

async function connect() {
  if (!CFG.found) {
    showOverlay('Link not available', 'This link has expired, been revoked, or never existed. Ask whoever shared it for a new one.', false);
    return;
  }
  attempts += 1;
  setStatus('connecting', false);
  if (retryTimer !== null) { clearTimeout(retryTimer); retryTimer = null; }
  if (connectDeadline !== null) { clearTimeout(connectDeadline); connectDeadline = null; }
  if (pc) { try { pc.close(); } catch (e) {} pc = null; }

  // This function awaits five times, and every one of them is a window in which
  // it can be called again -- the Retry button, an unlock, the 409 timer. It used
  // to read and write the module-level `pc` throughout, so a second attempt would
  // replace `pc` while the first was still in its fetch; the first then applied
  // ITS answer to the second attempt's connection, and the second's own
  // setRemoteDescription found the thing already stable and threw
  // `InvalidStateError: Called in wrong state: stable`. The attempt died there,
  // leaving a black rectangle.
  //
  // So each attempt takes a generation and its OWN reference. `self` is what
  // every line below touches; `pc` exists only so the next attempt can close the
  // previous one. A superseded attempt closes its own connection and returns
  // without touching a pixel of UI that now belongs to somebody else.
  const gen = ++generation;
  const self = new RTCPeerConnection({ iceServers: CFG.iceServers });
  pc = self;
  const stale = () => gen !== generation;
  const abandon = () => { try { self.close(); } catch (e) {} };

  // Receive-only, both kinds. Declared up front rather than waiting for tracks:
  // the offer has to advertise what we are willing to receive, and an offer with
  // no media sections gets an answer with no media sections.
  self.addTransceiver('video', { direction: 'recvonly' });
  self.addTransceiver('audio', { direction: 'recvonly' });

  const inbound = new MediaStream();
  self.ontrack = (e) => {
    if (stale()) return;
    inbound.addTrack(e.track);
    video.srcObject = inbound;
    video.play().catch(() => {
      // Autoplay with audio is blocked until a gesture. Muting is the wrong fix
      // (it silently drops the audio the host chose to send), so ask instead.
      showOverlay('Ready to play', 'Your browser blocked autoplay. Click to start watching.', false);
      retryBtn.textContent = 'Play';
    });
  };
  self.onconnectionstatechange = () => {
    // A superseded attempt still fires these as it tears down. Reporting them
    // would let a dead connection overwrite the live one's status.
    if (stale()) return;
    if (self.connectionState === 'connected') {
      if (connectDeadline !== null) { clearTimeout(connectDeadline); connectDeadline = null; }
      hideOverlay();
      setStatus('live', true);
    }
    if (self.connectionState === 'failed') {
      setStatus('disconnected', false);
      showOverlay('Connection lost', 'The stream dropped. It may come back on its own.', false);
    }
  };

  await self.setLocalDescription(await self.createOffer());
  if (stale()) return abandon();

  await new Promise((resolve) => {
    // Wait for ICE gathering: this is a one-shot HTTP exchange with no trickle
    // channel, so an offer sent before gathering finishes carries no candidates
    // and connects only in the luckiest network conditions.
    if (self.iceGatheringState === 'complete') return resolve();
    const check = () => {
      if (self.iceGatheringState === 'complete') {
        self.removeEventListener('icegatheringstatechange', check);
        resolve();
      }
    };
    self.addEventListener('icegatheringstatechange', check);
    setTimeout(resolve, 2500);
  });
  if (stale()) return abandon();

  let res;
  try {
    res = await fetch('/whep/' + CFG.token, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp', 'X-Share-Passphrase': passphrase },
      body: self.localDescription.sdp,
    });
  } catch (err) {
    if (stale()) return abandon();
    setStatus('offline', false);
    showOverlay('Cannot reach the relay', String(err), false);
    return;
  }
  if (stale()) return abandon();

  if (res.status === 403) {
    setStatus('locked', false);
    showOverlay('Passphrase needed', 'This stream is protected. Enter the passphrase you were given.', true);
    return;
  }
  if (res.status === 409) {
    setStatus('waiting', false);
    showOverlay('Not started yet', 'The link works — the host has not started sharing. This page will keep checking.', false);
    retryTimer = setTimeout(connect, 4000);
    return;
  }
  if (res.status === 503) {
    setStatus('full', false);
    showOverlay('Stream is full', 'This relay caps how many people can watch one stream at once. Try again shortly.', false);
    return;
  }
  if (!res.ok) {
    setStatus('unavailable', false);
    showOverlay('Link not available', 'This link has expired, been revoked, or never existed.', false);
    return;
  }

  const answer = await res.text();
  if (stale()) return abandon();

  try {
    await self.setRemoteDescription({ type: 'answer', sdp: answer });
  } catch (err) {
    // Reachable if this attempt was superseded between the check above and here.
    // Surfaced rather than left as an unhandled rejection, which is what a black
    // screen with an angry-looking console used to be made of.
    if (stale()) return abandon();
    setStatus('failed', false);
    showOverlay('Could not start playback', String(err), false);
    return;
  }

  // NOT 'live' yet. An SDP answer means the relay agreed to send; it says
  // nothing about whether a path between us exists. Declaring victory here (and
  // hiding the overlay) is what turned a viewer whose ICE never completed into a
  // silent black rectangle with a green 'live' chip -- the single most confusing
  // state this page can be in, and indistinguishable from a host sharing a black
  // screen. `onconnectionstatechange` promotes us to 'live' when a path actually
  // forms; until then the overlay stays up and says what is happening.
  setStatus('negotiated', false);
  overlayTitle.textContent = 'Connecting';
  overlayText.textContent = 'The relay answered. Finding a network path…';

  // ICE can sit in 'checking' for a long time and may settle on 'disconnected'
  // rather than 'failed', in which case the failure handler never runs at all.
  // So the page gives up on its own schedule and says something actionable.
  if (connectDeadline !== null) clearTimeout(connectDeadline);
  connectDeadline = setTimeout(() => {
    if (stale()) return;
    if (self.connectionState === 'connected') return;
    setStatus('no path', false);
    showOverlay(
      'Could not reach the stream',
      'The relay answered but no connection formed — usually a restrictive ' +
      'network on one end. Try another network, or ask the host to enable TURN ' +
      'for viewers.',
      false,
    );
  }, CONNECT_TIMEOUT_MS);
}

retryBtn.addEventListener('click', () => {
  if (retryBtn.textContent === 'Play') { video.play(); hideOverlay(); return; }
  connect();
});
$('unlock').addEventListener('click', () => {
  passphrase = passInput.value;
  connect();
});
passInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') $('unlock').click(); });

// --- chat ------------------------------------------------------------------

let ws = null;
let myName = '';

function addMessage(who, text) {
  const el = document.createElement('div');
  el.className = 'msg';
  const w = document.createElement('span');
  w.className = 'who';
  w.textContent = who;
  // textContent, never innerHTML: every word here came from a stranger.
  const body = document.createTextNode(text);
  el.appendChild(w);
  el.appendChild(body);
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}

function openChat() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(proto + '://' + location.host + '/chat/' + CFG.token);
  ws.onopen = () => sysLine('Connected to chat.');
  ws.onclose = () => sysLine('Chat disconnected.');
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.kind === 'chat') addMessage(m.name, m.text);
    else if (m.kind === 'system') sysLine(m.text);
  };
}

$('send').addEventListener('click', () => {
  const input = $('say');
  const text = input.value.trim();
  if (!text || !ws || ws.readyState !== 1) return;
  if (!myName) myName = ($('who').value.trim() || 'guest').slice(0, 24);
  ws.send(JSON.stringify({ name: myName, text }));
  input.value = '';
});
$('say').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('send').click(); });

openChat();
connect();
"""


def _page(body: str, script: str, config: dict[str, object]) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        # A public share link should not turn into a search result or a referrer
        # trail. Neither header is a security boundary; both are the polite
        # default for a page whose URL is the credential.
        "<meta name='robots' content='noindex, nofollow'>"
        "<meta name='referrer' content='no-referrer'>"
        "<title>Shared screen</title>"
        f"<style>{_STYLE}</style></head><body>{body}"
        f"<script>window.__SHARE__ = {json.dumps(config)};</script>"
        f"<script>{script}</script></body></html>"
    )


def render(
    *,
    token: str,
    title: str,
    found: bool,
    needs_passphrase: bool,
    live: bool,
) -> str:
    """The viewer page for one token."""
    safe_title = html.escape(title) if title else "Shared screen"
    body = f"""
<header>
  <span class='title'>{safe_title}</span>
  <span class='spacer'></span>
  <span class='chip off' id='status'><span class='dot'></span>connecting</span>
</header>
<main>
  <div class='stage'>
    <video id='video' playsinline autoplay></video>
    <div class='overlay' id='overlay'>
      <h1 id='overlay-title'>Connecting</h1>
      <p id='overlay-text'>Reaching the relay.</p>
      <div id='pass-row' style='display:none; gap:6px;'>
        <input id='pass' type='password' placeholder='passphrase' autocomplete='off'>
        <button id='unlock'>Watch</button>
      </div>
      <button id='retry' class='ghost'>Retry</button>
    </div>
  </div>
  <aside>
    <div class='head'>Chat</div>
    <div id='log'></div>
    <div class='compose'>
      <input id='who' placeholder='name' maxlength='24' autocomplete='off'>
    </div>
    <div class='compose'>
      <input id='say' placeholder='Say something…' maxlength='500' autocomplete='off'>
      <button id='send'>Send</button>
    </div>
  </aside>
</main>
<footer>You are watching a shared screen. Viewers can watch and chat — nothing else.</footer>
"""
    config = {
        "token": token,
        "found": found,
        "needsPassphrase": needs_passphrase,
        "live": live,
        # From the relay's own config, so the two ends of the connection agree.
        # STUN only unless the operator opts in: a public viewer is a stranger,
        # and a TURN credential in a page anyone can open makes the operator's
        # bandwidth free for the whole internet. See `ice.viewer_ice`.
        "iceServers": ice.viewer_ice(),
    }
    return _page(body, _SCRIPT, config)


def render_index() -> str:
    """The relay's root. Deliberately says nothing about who is streaming."""
    body = """
<header><span class='title'>horrible share relay</span></header>
<main>
  <div class='stage'>
    <div class='overlay'>
      <h1>Nothing to see here</h1>
      <p>This is a relay for shared screens. A share link looks like
         <code>/s/&lt;token&gt;</code> and is handed out by the person sharing.</p>
    </div>
  </div>
</main>
"""
    return _page(body, "", {})
