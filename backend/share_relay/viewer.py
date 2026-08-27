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
  /* Exactly one viewport tall and never taller. `min-height` let the page grow
     past the window the moment anything inside it did -- the chat log and the
     diagnostics panel both can -- and a shared screen that scrolls off the
     bottom is a shared screen you cannot see. `dvh` second so mobile browsers
     measure against the *visible* viewport rather than the one hiding behind
     the URL bar; the vh line stays as the fallback for engines without it. */
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  display: flex; flex-direction: column;
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
  background: #000; min-width: 0; min-height: 0; position: relative; }
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
  display: flex; flex-direction: column; min-height: 0; overflow: hidden;
}
aside .head { padding: 9px 12px; border-bottom: 1px solid var(--border);
  font-size: 11px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
  color: var(--dim); }
/* `min-height` so the log never collapses to a sliver when the diagnostics
   panel is open on a short window -- it is allowed to be small, but a chat you
   cannot read one line of may as well not be rendered. It still scrolls rather
   than pushing the composer off the bottom, because `aside` clips. */
#log { flex: 1 1 auto; min-height: 54px; overflow-y: auto; padding: 10px 12px;
  display: flex; flex-direction: column; gap: 8px; }
.msg { font-size: 12.5px; line-height: 1.45; word-break: break-word; }
.msg .who { font-family: var(--mono); font-size: 11px; color: var(--accent);
  display: block; }
.msg.sys { color: var(--dim); font-style: italic; }
.compose { display: flex; gap: 6px; padding: 10px 12px; border-top: 1px solid var(--border); }
.compose input { flex: 1; min-width: 0; }
footer { padding: 7px 16px; border-top: 1px solid var(--border);
  font-family: var(--mono); font-size: 11px; color: var(--dim); }
/* --- connection progress --------------------------------------------------
   The point is that someone waiting can see something happening. A spinner says
   "alive"; this says which of six things is being waited on, which is also the
   first thing anyone debugging a stuck viewer wants to know. */
.progress { width: min(340px, 78vw); display: flex; flex-direction: column; gap: 7px; }
.track {
  height: 4px; border-radius: 999px; background: rgba(255,255,255,0.09);
  overflow: hidden; position: relative;
}
.bar {
  height: 100%; width: 0%; border-radius: 999px; background: var(--accent);
  transition: width 420ms cubic-bezier(0.22, 0.61, 0.36, 1);
}
/* Waiting on the host is not progress, and a bar creeping toward 100% would be
   a lie about something that has not started. It sweeps instead. */
.track.waiting .bar {
  width: 35%; background: var(--warn);
  animation: sweep 1.5s ease-in-out infinite;
}
@keyframes sweep {
  0%   { transform: translateX(-110%); }
  100% { transform: translateX(400%); }
}
.phase {
  display: flex; justify-content: space-between; gap: 10px;
  font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--dim);
}
.phase .count { opacity: 0.65; }
@media (prefers-reduced-motion: reduce) {
  .bar { transition: none; }
  .track.waiting .bar { animation: none; width: 100%; }
}

/* --- diagnostics ---------------------------------------------------------- */
.diag { border-top: 1px solid var(--border); font-size: 11px; }
.diag > summary {
  padding: 7px 12px; cursor: pointer; color: var(--dim); user-select: none;
  font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.1em;
  text-transform: uppercase; display: flex; align-items: center; gap: 6px;
}
.diag > summary::marker { color: var(--border); }
.diag .body { max-height: 30vh; overflow: auto; padding: 0 12px 8px; }
.diag pre {
  margin: 0; font-family: var(--mono); font-size: 10.5px; line-height: 1.5;
  color: var(--dim); white-space: pre-wrap; word-break: break-word;
}
.diag .tools { display: flex; gap: 6px; padding: 0 12px 10px; }
.diag button { height: 26px; font-size: 11px; padding: 0 10px; }

@media (max-width: 760px) {
  main { flex-direction: column; }
  aside { width: auto; border-left: none; border-top: 1px solid var(--border);
    /* A share of a screen is the point; the chat is secondary. Capped so the
       stage keeps most of a short window. */
    max-height: 42vh; flex: none; }
  .diag .body { max-height: 18vh; }
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
const bar = $('bar');
const track = $('track');
const phaseLabel = $('phase-label');
const phaseCount = $('phase-count');
const diagLog = $('diag-log');

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
let statsTimer = null;
let countdownTimer = null;

// --- diagnostics -----------------------------------------------------------
//
// Everything this page does, timestamped, in the page itself.
//
// This exists because diagnosing it from the outside meant reading the relay's
// Fly logs, and the relay can only see requests -- it cannot see ICE failing,
// a track arriving, autoplay being blocked, or a connection state churning,
// which is where every real failure has been so far. A viewer who says "it is
// black" can now open one panel and copy the whole story instead.
//
// A bounded buffer, because this runs for as long as someone watches.
const DIAG_MAX = 300;
const diag = [];
const startedAt = Date.now();

function diagLine(msg) {
  const t = ((Date.now() - startedAt) / 1000).toFixed(2).padStart(7, ' ');
  const line = t + 's  ' + msg;
  diag.push(line);
  if (diag.length > DIAG_MAX) diag.shift();
  // Mirrored to the console so it interleaves with whatever else the browser is
  // complaining about, which is how the last three bugs actually surfaced.
  console.log('[share] ' + line);
  if (diagLog) {
    diagLog.textContent = diag.join('\n');
    const body = diagLog.parentElement;
    if (body) body.scrollTop = body.scrollHeight;
  }
}

// --- connection phases -----------------------------------------------------
//
// Named steps rather than a spinner: someone waiting can see that something is
// happening AND which thing, and "stuck at gathering" versus "stuck at relay"
// is the first question worth asking about a viewer that never starts.
const PHASES = [
  ['offer', 'Preparing'],
  ['gather', 'Finding your network'],
  ['relay', 'Contacting relay'],
  ['negotiate', 'Negotiating'],
  ['path', 'Connecting'],
  ['live', 'Playing'],
];

function setPhase(key, note) {
  const i = PHASES.findIndex((p) => p[0] === key);
  if (i < 0) return;
  const pct = Math.round(((i + 1) / PHASES.length) * 100);
  track.classList.remove('waiting');
  bar.style.width = pct + '%';
  bar.setAttribute('aria-valuenow', String(pct));
  phaseLabel.textContent = note || PHASES[i][1];
  phaseCount.textContent = (i + 1) + '/' + PHASES.length;
  diagLine('phase ' + key + ' (' + pct + '%)' + (note ? ' - ' + note : ''));
}

/** Waiting on the host is not progress -- so it sweeps rather than advances. */
function setWaiting(note) {
  track.classList.add('waiting');
  bar.removeAttribute('aria-valuenow');
  phaseLabel.textContent = note;
  phaseCount.textContent = '';
}

function stopCountdown() {
  if (countdownTimer !== null) { clearInterval(countdownTimer); countdownTimer = null; }
}

/** Count the retry down out loud, so the page never looks abandoned. */
function countdown(seconds, note) {
  stopCountdown();
  let left = seconds;
  const tick = () => {
    setWaiting(note + ' - retrying in ' + left + 's');
    if (left <= 0) return stopCountdown();
    left -= 1;
  };
  tick();
  countdownTimer = setInterval(tick, 1000);
}

// --- live quality sampling -------------------------------------------------
//
// The numbers that actually diagnose "it is laggy": frame rate, freezes, loss,
// round trip, and which candidate pair won. Logged periodically so a report
// from a viewer carries evidence rather than an adjective.
async function sampleStats(reason) {
  if (!pc) return;
  try {
    const stats = await pc.getStats();
    let v = null, pair = null, local = null, remote = null;
    const cands = {};
    stats.forEach((r) => {
      if (r.type === 'inbound-rtp' && r.kind === 'video') v = r;
      if (r.type === 'candidate-pair' && r.nominated && r.state === 'succeeded') pair = r;
      if (r.type === 'local-candidate' || r.type === 'remote-candidate') cands[r.id] = r;
    });
    if (pair) { local = cands[pair.localCandidateId]; remote = cands[pair.remoteCandidateId]; }
    const bits = [];
    if (v) {
      bits.push(v.frameWidth + 'x' + v.frameHeight);
      bits.push((v.framesPerSecond === undefined ? '?' : v.framesPerSecond) + 'fps');
      bits.push('decoded=' + v.framesDecoded);
      bits.push('dropped=' + (v.framesDropped || 0));
      bits.push('freezes=' + (v.freezeCount || 0));
      bits.push('lost=' + (v.packetsLost || 0));
      if (v.jitter !== undefined) bits.push('jitter=' + Math.round(v.jitter * 1000) + 'ms');
      if (v.jitterBufferEmittedCount) {
        bits.push('buffer=' + Math.round((v.jitterBufferDelay / v.jitterBufferEmittedCount) * 1000) + 'ms');
      }
    } else {
      bits.push('no inbound video yet');
    }
    if (pair && pair.currentRoundTripTime !== undefined) {
      bits.push('rtt=' + Math.round(pair.currentRoundTripTime * 1000) + 'ms');
    }
    if (local && remote) bits.push('path=' + local.candidateType + '/' + remote.candidateType);
    diagLine('stats(' + reason + ') ' + bits.join(' '));
  } catch (err) {
    diagLine('stats failed: ' + err);
  }
}

function startStats() {
  stopStats();
  statsTimer = setInterval(() => void sampleStats('periodic'), 10000);
  void sampleStats('connected');
}

function stopStats() {
  if (statsTimer !== null) { clearInterval(statsTimer); statsTimer = null; }
}

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
  stopCountdown();
  stopStats();
  showOverlay('Connecting', 'Setting up the connection.', false);
  setPhase('offer');
  diagLine('connect attempt #' + attempts);
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

  self.oniceconnectionstatechange = () => {
    if (stale()) return;
    diagLine('ice ' + self.iceConnectionState);
  };
  self.onicegatheringstatechange = () => {
    if (stale()) return;
    diagLine('gathering ' + self.iceGatheringState);
  };
  self.onicecandidate = (e) => {
    if (stale() || !e.candidate) return;
    // Type only. A candidate line carries the viewer's own addresses, and this
    // panel is meant to be copied to somebody else.
    diagLine('candidate ' + (e.candidate.type || '?') + '/' + (e.candidate.protocol || '?'));
  };

  const inbound = new MediaStream();
  self.ontrack = (e) => {
    if (stale()) return;
    diagLine('track ' + e.track.kind + ' arrived');
    inbound.addTrack(e.track);
    video.srcObject = inbound;
    video.play().catch((err) => {
      // Autoplay with audio is blocked until a gesture. Muting is the wrong fix
      // (it silently drops the audio the host chose to send), so ask instead.
      diagLine('autoplay blocked: ' + err);
      showOverlay('Ready to play', 'Your browser blocked autoplay. Click to start watching.', false);
      $('progress').style.display = 'none';
      retryBtn.textContent = 'Play';
    });
  };
  self.onconnectionstatechange = () => {
    // A superseded attempt still fires these as it tears down. Reporting them
    // would let a dead connection overwrite the live one's status.
    if (stale()) return;
    diagLine('connection ' + self.connectionState);
    if (self.connectionState === 'connected') {
      if (connectDeadline !== null) { clearTimeout(connectDeadline); connectDeadline = null; }
      setPhase('live');
      hideOverlay();
      setStatus('live', true);
      startStats();
    }
    if (self.connectionState === 'failed') {
      stopStats();
      setStatus('disconnected', false);
      showOverlay('Connection lost', 'The stream dropped. It may come back on its own.', false);
    }
  };

  await self.setLocalDescription(await self.createOffer());
  if (stale()) return abandon();
  setPhase('gather');

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
  setPhase('relay');

  let res;
  try {
    res = await fetch('/whep/' + CFG.token, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp', 'X-Share-Passphrase': passphrase },
      body: self.localDescription.sdp,
    });
  } catch (err) {
    if (stale()) return abandon();
    diagLine('relay unreachable: ' + err);
    setStatus('offline', false);
    showOverlay('Cannot reach the relay', String(err), false);
    return;
  }
  if (stale()) return abandon();
  diagLine('relay answered ' + res.status);

  if (res.status === 403) {
    setStatus('locked', false);
    showOverlay('Passphrase needed', 'This stream is protected. Enter the passphrase you were given.', true);
    return;
  }
  if (res.status === 409) {
    setStatus('waiting', false);
    showOverlay('Not started yet', 'The link works — the host has not started sharing. This page will keep checking.', false);
    countdown(4, 'Waiting for the host');
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
  setPhase('negotiate');

  try {
    await self.setRemoteDescription({ type: 'answer', sdp: answer });
  } catch (err) {
    // Reachable if this attempt was superseded between the check above and here.
    // Surfaced rather than left as an unhandled rejection, which is what a black
    // screen with an angry-looking console used to be made of.
    if (stale()) return abandon();
    diagLine('setRemoteDescription failed: ' + err);
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
  setPhase('path', 'Finding a path');
  overlayTitle.textContent = 'Connecting';
  overlayText.textContent = 'The relay answered. Finding a network path…';

  // ICE can sit in 'checking' for a long time and may settle on 'disconnected'
  // rather than 'failed', in which case the failure handler never runs at all.
  // So the page gives up on its own schedule and says something actionable.
  if (connectDeadline !== null) clearTimeout(connectDeadline);
  connectDeadline = setTimeout(() => {
    if (stale()) return;
    if (self.connectionState === 'connected') return;
    diagLine('no path after ' + (CONNECT_TIMEOUT_MS / 1000) + 's; state=' + self.connectionState);
    void sampleStats('timeout');
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

$('diag-copy').addEventListener('click', () => {
  const text = [
    'share viewer diagnostics',
    'token: ' + CFG.token,
    'agent: ' + navigator.userAgent,
    'ice:   ' + JSON.stringify((CFG.iceServers || []).map((s) => s.urls)),
    '',
  ].join('\n') + diag.join('\n');
  const btn = $('diag-copy');
  navigator.clipboard.writeText(text).then(
    () => { btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = 'Copy'; }, 1500); },
    () => { btn.textContent = 'Copy failed'; },
  );
});
$('diag-stats').addEventListener('click', () => void sampleStats('manual'));

// A viewer that closes its tab otherwise keeps its slot on the relay until ICE
// consent expires ~30s later -- and a slot is a full-resolution encoder on a
// machine whose first ceiling is memory. Closing the connection makes the
// relay notice immediately.
addEventListener('pagehide', () => { stopStats(); if (pc) { try { pc.close(); } catch (e) {} } });

diagLine('page loaded; found=' + CFG.found + ' turn=' +
  (CFG.iceServers || []).some((s) => String(s.urls).indexOf('turn') === 0));
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
      <div class='progress' id='progress'>
        <div class='track' id='track'>
          <div class='bar' id='bar' role='progressbar'
               aria-valuemin='0' aria-valuemax='100' aria-valuenow='0'
               aria-label='Connection progress'></div>
        </div>
        <div class='phase'>
          <span id='phase-label'>Starting</span>
          <span class='count' id='phase-count'></span>
        </div>
      </div>
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
    <details class='diag' id='diag'>
      <summary>Diagnostics</summary>
      <div class='body'><pre id='diag-log'></pre></div>
      <div class='tools'>
        <button class='ghost' id='diag-copy'>Copy</button>
        <button class='ghost' id='diag-stats'>Sample now</button>
      </div>
    </details>
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
