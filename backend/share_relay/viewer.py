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

/* --- on-video controls -----------------------------------------------------
   Two buttons, bottom right, and only once a picture exists: sound and
   fullscreen are the only things a viewer does to the video itself. Faded until
   the pointer is over the stage, except the unmute prompt, which has to be
   seen -- it is the one thing standing between the viewer and the audio. */
.controls { position: absolute; right: 12px; bottom: 12px; display: flex; gap: 6px;
  opacity: 0; transition: opacity 160ms ease; }
.stage:hover .controls, .controls:focus-within, .controls.show { opacity: 1; }
.controls.hidden { display: none; }
.ctl { height: 32px; min-width: 32px; padding: 0 10px; display: inline-flex;
  align-items: center; justify-content: center; gap: 7px;
  background: rgba(14,16,20,0.78); border: 1px solid var(--border); color: var(--text);
  font-family: var(--mono); font-size: 11px; letter-spacing: 0.08em;
  text-transform: uppercase; }
.ctl:hover { border-color: var(--accent); }
.ctl svg { width: 15px; height: 15px; flex: none; }
.ctl.prompt { border-color: var(--accent); border-top: 2px solid var(--accent); }
@media (hover: none) { .controls { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .controls { transition: none; } }

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
// Whether a frame has actually decoded on this attempt. The page is only ever
// 'live' when this is true -- see `watchFirstFrame`.
let gotFrame = false;
// Bumped by every connect() attempt. An attempt whose generation is no longer
// current has been superseded and must touch neither `pc` nor the UI.
let generation = 0;
let statsTimer = null;
let countdownTimer = null;
// The link is dead (revoked, expired, or the relay forgot it). Nothing retries
// after this -- a page that keeps knocking on a dead link is just noise.
let ended = false;
// The host stopped sharing and the relay said so. The page waits for the
// relay's `live` push rather than hammering WHEP, with a slow poll as backstop.
let paused = false;
// Waiting on a passphrase. Pushes are ignored meanwhile, or a host coming back
// would wipe the prompt out from under someone typing into it.
let locked = false;
// Consecutive reconnects since the last decoded frame, for the backoff.
let reconnects = 0;
// A 'disconnected' peer connection often recovers by itself; this is how long
// it gets before the page stops waiting and dials again.
const DISCONNECT_GRACE_MS = 4000;
let graceTimer = null;
// Whether the chat socket is up. While it is, the relay pushes stream state,
// so waiting for the host can poll slowly instead of every few seconds.
let chatOpen = false;
// The ICE servers for the next attempt. Seeded from the page and refreshed
// before every connect(): a TURN credential is minted with a lifetime, and a
// page left open past it would otherwise redial with a dead one.
let iceServers = CFG.iceServers || [];
// Automatic redials spent on "connected, but no frame". The relay holds and
// repeats the newest frame, so a working path shows a picture within a second;
// one that does not is a broken leg worth one or two fresh tries before asking
// a human to do anything.
let noFrameRetries = 0;
const NO_FRAME_RETRIES = 2;
// The browser refused to autoplay with sound, so the page is playing muted and
// the unmute button is prompting for the click that will unlock it.
let autoMuted = false;

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

/**
 * Go 'live' on the first frame that actually decodes, and never before.
 *
 * `loadeddata` is the portable "a frame exists" signal; engines with
 * `requestVideoFrameCallback` give the same answer one frame earlier. Both are
 * registered because neither fires for a track that carries no media -- which is
 * the entire point. A stream that never produces a picture keeps saying so
 * instead of quietly going green.
 */
function watchFirstFrame(stale) {
  const arrive = () => {
    if (gotFrame || stale()) return;
    gotFrame = true;
    if (connectDeadline !== null) { clearTimeout(connectDeadline); connectDeadline = null; }
    diagLine('first frame decoded');
    reconnects = 0;
    noFrameRetries = 0;
    setPhase('live');
    hideOverlay();
    setStatus('live', true);
    showControls(true);
  };
  if ('requestVideoFrameCallback' in video) video.requestVideoFrameCallback(arrive);
  video.addEventListener('loadeddata', arrive, { once: true });
  // A reconnect can land on an element that already holds a decoded frame, and
  // neither event fires again for it.
  if (video.readyState >= 2) arrive();
}

/** Drop whatever attempt is running, including one still mid-await. */
function tearDown() {
  generation += 1; // every in-flight attempt is now stale
  if (pc) { try { pc.close(); } catch (e) {} pc = null; }
  if (retryTimer !== null) { clearTimeout(retryTimer); retryTimer = null; }
  if (connectDeadline !== null) { clearTimeout(connectDeadline); connectDeadline = null; }
  if (graceTimer !== null) { clearTimeout(graceTimer); graceTimer = null; }
  stopCountdown();
  stopStats();
  gotFrame = false;
  video.srcObject = null;
  showControls(false);
}

/**
 * Dial again after a dropped connection, backing off: 2s, 4s, 8s, then every 15s.
 *
 * This used to be a Retry button. A viewer whose Wi-Fi blinked had to notice
 * the stream had stopped, find the button and press it -- and somebody watching
 * a screen share is, by definition, not looking at the controls.
 */
function scheduleReconnect(reason) {
  if (ended || paused) return;
  tearDown();
  const delay = Math.min(2000 * Math.pow(2, reconnects), 15000);
  reconnects += 1;
  diagLine('reconnecting in ' + (delay / 1000) + 's: ' + reason);
  setStatus('reconnecting', false);
  showOverlay('Reconnecting', reason, false);
  $('progress').style.display = '';
  countdown(Math.round(delay / 1000), 'Reconnecting');
  retryTimer = setTimeout(connect, delay);
}

/** The link is dead. Says so once and stops every timer that would retry it. */
function endLink() {
  if (ended) return;
  ended = true;
  tearDown();
  diagLine('link ended');
  setStatus('ended', false);
  showOverlay('This share has ended', 'The link was revoked or expired. Ask whoever shared it for a new one.', false);
  $('progress').style.display = 'none';
  retryBtn.style.display = 'none';
}

/** The relay pushed a change in the stream's state over the chat socket. */
function onStreamEvent(state) {
  diagLine('relay says: ' + state);
  if (state === 'ended') return endLink();
  if (ended || locked) return;
  if (state === 'paused') {
    paused = true;
    tearDown();
    setStatus('paused', false);
    showOverlay('Paused', 'The host stopped sharing. The stream comes back here on its own when they start again.', false);
    $('progress').style.display = '';
    setWaiting('Waiting for the host');
    // Backstop for a push that never arrives (the chat socket dropped).
    retryTimer = setTimeout(connect, 30000);
    return;
  }
  if (state === 'live') {
    // Already watching: the relay keeps a viewer's video attached across a host
    // re-publishing, so the picture resumes on its own and redialling would only
    // blank it for a second.
    if (gotFrame && pc && pc.connectionState === 'connected') return;
    paused = false;
    reconnects = 0;
    connect();
  }
}

// --- on-video controls -----------------------------------------------------

const ICON_SOUND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/></svg>';
const ICON_MUTED = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4z"/><path d="m22 9-6 6"/><path d="m16 9 6 6"/></svg>';
const ICON_FULL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9V4h5"/><path d="M20 9V4h-5"/><path d="M4 15v5h5"/><path d="M20 15v5h-5"/></svg>';
const ICON_EXIT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 4v5H4"/><path d="M15 4v5h5"/><path d="M9 20v-5H4"/><path d="M15 20v-5h5"/></svg>';

const controls = $('controls');
const muteBtn = $('mute');
const fullBtn = $('full');
const stage = document.querySelector('.stage');

function renderControls() {
  // Labelled only when it is asking for something. A muted-by-the-browser
  // stream says "Unmute" in words; the rest of the time an icon is enough.
  muteBtn.innerHTML = (video.muted ? ICON_MUTED : ICON_SOUND) + (autoMuted ? '<span>Unmute</span>' : '');
  muteBtn.classList.toggle('prompt', autoMuted);
  muteBtn.setAttribute('aria-label', video.muted ? 'Unmute' : 'Mute');
  controls.classList.toggle('show', autoMuted);
  const full = Boolean(document.fullscreenElement);
  fullBtn.innerHTML = full ? ICON_EXIT : ICON_FULL;
  fullBtn.setAttribute('aria-label', full ? 'Exit fullscreen' : 'Fullscreen');
}

function showControls(on) {
  controls.classList.toggle('hidden', !on);
  if (on) renderControls();
}

function toggleFullscreen() {
  if (document.fullscreenElement) { document.exitFullscreen().catch(() => {}); return; }
  if (stage.requestFullscreen) { stage.requestFullscreen().catch(() => {}); return; }
  // iOS Safari: no element fullscreen, only the video's own player.
  if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
}

muteBtn.addEventListener('click', () => {
  video.muted = !video.muted;
  autoMuted = false;
  if (!video.muted) video.play().catch(() => {});
  renderControls();
});
fullBtn.addEventListener('click', toggleFullscreen);
video.addEventListener('dblclick', toggleFullscreen);
document.addEventListener('fullscreenchange', renderControls);

/** Fetch this link's current ICE servers. Keeps the last set on any failure. */
async function refreshIce() {
  // Gated like WHEP, and a wrong passphrase is charged there too. Asking with
  // none on a protected link would spend a second guess per attempt for nothing.
  if (CFG.needsPassphrase && !passphrase) return;
  try {
    const res = await fetch('/whep/' + CFG.code + '/ice', {
      headers: { 'X-Share-Passphrase': passphrase },
    });
    if (!res.ok) return; // WHEP itself will report a locked or dead link.
    const body = await res.json();
    if (Array.isArray(body.iceServers)) iceServers = body.iceServers;
  } catch (e) {
    // The relay is waking or unreachable; WHEP will say which.
  }
}

async function connect() {
  if (!CFG.found) {
    showOverlay('Link not available', 'This link has expired, been revoked, or never existed. Ask whoever shared it for a new one.', false);
    return;
  }
  if (ended) return;
  attempts += 1;
  paused = false;
  setStatus('connecting', false);
  stopCountdown();
  stopStats();
  showControls(false);
  showOverlay('Connecting', 'Setting up the connection.', false);
  // A previous attempt may have hidden these for its own prompt.
  $('progress').style.display = '';
  retryBtn.textContent = 'Retry';
  setPhase('offer');
  diagLine('connect attempt #' + attempts);
  if (retryTimer !== null) { clearTimeout(retryTimer); retryTimer = null; }
  if (connectDeadline !== null) { clearTimeout(connectDeadline); connectDeadline = null; }
  if (graceTimer !== null) { clearTimeout(graceTimer); graceTimer = null; }
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
  gotFrame = false;
  await refreshIce();
  if (gen !== generation) return;
  const self = new RTCPeerConnection({ iceServers });
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
      if (stale()) return;
      // Autoplay with sound is blocked until a gesture. Play muted rather than
      // holding the picture hostage to a click, and put a labelled Unmute
      // button on the video: the audio is one click away and visibly so, which
      // is what made muting the wrong fix back when it was silent.
      diagLine('autoplay with sound blocked, playing muted: ' + err);
      video.muted = true;
      autoMuted = true;
      video.play().then(() => { if (gotFrame) renderControls(); }).catch((err2) => {
        if (stale()) return;
        // Even muted autoplay refused (data saver, a strict policy): ask.
        diagLine('muted autoplay blocked too: ' + err2);
        showOverlay('Ready to play', 'Your browser blocked autoplay. Click to start watching.', false);
        $('progress').style.display = 'none';
        retryBtn.textContent = 'Play';
      });
    });
  };
  self.onconnectionstatechange = () => {
    // A superseded attempt still fires these as it tears down. Reporting them
    // would let a dead connection overwrite the live one's status.
    if (stale()) return;
    diagLine('connection ' + self.connectionState);
    if (self.connectionState === 'connected') {
      // Deliberately does NOT hide the overlay, clear the deadline, or go green.
      //
      // A peer connection reaches 'connected' when ICE and DTLS complete, which
      // says the two ends can talk -- not that one byte of media crossed. The
      // relay answers a WHEP offer for a room whose track is yielding nothing
      // just as happily as for a live one, so promoting to 'live' here produced
      // the exact failure this page exists to explain: a black rectangle under a
      // green 'live' chip, with the overlay that would have said so hidden by
      // the very event that proved nothing about the picture. The first decoded
      // frame is the only event that means "watching".
      setStatus('connected', false);
      setPhase('path', 'Waiting for video');
      startStats();
      watchFirstFrame(stale);
    }
    if (self.connectionState === 'connected' && graceTimer !== null) {
      clearTimeout(graceTimer);
      graceTimer = null;
    }
    if (self.connectionState === 'failed') {
      scheduleReconnect('The connection dropped.');
    }
    if (self.connectionState === 'disconnected' && graceTimer === null) {
      // Often a blip that ICE repairs by itself, so give it a moment first.
      graceTimer = setTimeout(() => {
        graceTimer = null;
        if (stale() || self.connectionState === 'connected') return;
        scheduleReconnect('The connection stalled.');
      }, DISCONNECT_GRACE_MS);
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
    res = await fetch('/whep/' + CFG.code, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp', 'X-Share-Passphrase': passphrase },
      body: self.localDescription.sdp,
    });
  } catch (err) {
    if (stale()) return abandon();
    // A network blip or a relay waking from scale-to-zero, both of which pass.
    abandon();
    scheduleReconnect('Cannot reach the relay (' + err + ').');
    return;
  }
  if (stale()) return abandon();
  diagLine('relay answered ' + res.status);

  if (res.status === 403) {
    abandon();
    locked = true;
    setStatus('locked', false);
    showOverlay('Passphrase needed', 'This stream is protected. Enter the passphrase you were given.', true);
    return;
  }
  if (res.status === 409) {
    abandon();
    let detail = '';
    try { detail = (await res.json()).detail || ''; } catch (e) {}
    if (stale()) return;
    setStatus('waiting', false);
    if (detail.indexOf('connecting') >= 0) {
      // The host has published but no frame has reached the relay yet. The
      // relay pushes `live` the moment one does.
      showOverlay('The host is connecting', 'Their stream reached the relay and the picture is on its way.', false);
    } else {
      showOverlay('Not started yet', 'The link works — the host has not started sharing. Video appears here the moment they do.', false);
    }
    // With the chat socket up the relay pushes `live` the instant the host
    // publishes, so this poll is only a backstop and can be slow. Without it,
    // polling is the only way to find out.
    const wait = chatOpen ? 20 : 4;
    if (chatOpen) setWaiting('Waiting for the host');
    else countdown(wait, 'Waiting for the host');
    retryTimer = setTimeout(connect, wait * 1000);
    return;
  }
  if (res.status === 503) {
    abandon();
    setStatus('full', false);
    showOverlay('Stream is full', 'This relay caps how many people can watch one stream at once. A seat is checked for every 15 seconds.', false);
    countdown(15, 'Waiting for a seat');
    retryTimer = setTimeout(connect, 15000);
    return;
  }
  if (res.status === 429) {
    abandon();
    setStatus('slow down', false);
    showOverlay('Too many attempts', 'This address tried too many wrong links or passphrases. Wait a minute, then retry.', false);
    return;
  }
  if (!res.ok) {
    abandon();
    endLink();
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
    // Guarded on a decoded FRAME, not on the connection state. Returning early
    // just because the peer connection said 'connected' is what let the silent
    // case run out the clock unreported: the one watchdog that could have named
    // it disarmed itself on the evidence of the thing that was not the problem.
    if (gotFrame) return;
    const connected = self.connectionState === 'connected';
    if (connected && noFrameRetries < NO_FRAME_RETRIES) {
      // The relay re-sends its newest frame at least once a second, so a
      // connected path with nothing on it is a broken leg, not a still screen.
      // A fresh connection usually fixes it; only after that does it need a human.
      noFrameRetries += 1;
      diagLine('connected but no frame; redialling (' + noFrameRetries + '/' + NO_FRAME_RETRIES + ')');
      void sampleStats('no-frame');
      scheduleReconnect('Connected, but no picture arrived. Trying again.');
      return;
    }
    diagLine((connected ? 'connected but no frame' : 'no path') +
      ' after ' + (CONNECT_TIMEOUT_MS / 1000) + 's; state=' + self.connectionState);
    void sampleStats('timeout');
    setStatus(connected ? 'no video' : 'no path', false);
    showOverlay(
      connected ? 'Connected, but no video' : 'Could not reach the stream',
      connected
        ? 'The connection is up but no picture arrived, even after redialling. ' +
          'Press Retry; if it persists, the network between you and the relay is ' +
          'dropping video — the diagnostics below say where.'
        : 'The relay answered but no connection formed — usually a restrictive ' +
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
  locked = false;
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

// Consecutive chat reconnects, for the backoff. The chat socket is also how the
// relay pushes stream state, so it is worth keeping up rather than letting one
// proxy hiccup turn the page back into a poller for the rest of the session.
let chatRetries = 0;

function openChat() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const sock = new WebSocket(proto + '://' + location.host + '/chat/' + CFG.code);
  ws = sock;
  sock.onopen = () => {
    const again = chatRetries > 0;
    chatRetries = 0;
    chatOpen = true;
    sysLine(again ? 'Chat reconnected.' : 'Connected to chat.');
    // Anything pushed while the socket was down was missed, so catch up.
    if (again && !gotFrame && !ended && !locked) connect();
  };
  sock.onclose = (e) => {
    chatOpen = false;
    if (ended) return;
    // 4404: the code names nothing any more -- revoked, expired, or a relay
    // that restarted and forgot it. 4429: this address is being rate limited,
    // and reconnecting would only extend that.
    if (e.code === 4404) return endLink();
    if (e.code === 4429) return sysLine('Chat refused: too many attempts from this address.');
    const delay = Math.min(1000 * Math.pow(2, chatRetries), 15000);
    chatRetries += 1;
    sysLine('Chat disconnected. Reconnecting…');
    setTimeout(openChat, delay);
  };
  sock.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.kind === 'chat') addMessage(m.name, m.text);
    else if (m.kind === 'system') sysLine(m.text);
    else if (m.kind === 'stream') onStreamEvent(m.state);
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
    'code:  ' + CFG.code,
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
// A dead code gets no chat socket: it would be refused, and every refusal is
// charged to this address as a guess.
if (CFG.found) openChat();
connect();
"""


def _page(
    body: str, script: str, config: dict[str, object], title: str = "Shared screen"
) -> str:
    """`title` must already be HTML-escaped."""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        # A public share link should not turn into a search result or a referrer
        # trail. Neither header is a security boundary; both are the polite
        # default for a page whose URL is the credential.
        "<meta name='robots' content='noindex, nofollow'>"
        "<meta name='referrer' content='no-referrer'>"
        f"<title>{title}</title>"
        # What a chat app's link preview shows when the link is pasted, which is
        # how nearly every one of these links travels. Without it the preview is
        # a bare hostname that says nothing about what clicking will do.
        f"<meta property='og:title' content='{title}'>"
        "<meta property='og:description' content='A live screen share. Opens in "
        "the browser; nothing to install.'>"
        "<meta name='theme-color' content='#0e1014'>"
        f"<style>{_STYLE}</style></head><body>{body}"
        f"<script>window.__SHARE__ = {json.dumps(config)};</script>"
        f"<script>{script}</script></body></html>"
    )


def render(
    *,
    code: str,
    title: str,
    found: bool,
    needs_passphrase: bool,
    live: bool,
) -> str:
    """The viewer page for one watch code.

    Never handed the stream's token. This page is public, and the token is
    publish authority: a viewer page that carried it let anyone watching replace
    the host's stream with their own.
    """
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
    <div class='controls hidden' id='controls'>
      <button class='ctl' id='mute' type='button' aria-label='Mute'></button>
      <button class='ctl' id='full' type='button' aria-label='Fullscreen'></button>
    </div>
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
        "code": code,
        "found": found,
        "needsPassphrase": needs_passphrase,
        "live": live,
        # From the relay's own config, so the two ends of the connection agree.
        # Minted (expiring) TURN is included; a static TURN credential only when
        # the operator opts in, since a page anyone can open would make that
        # bandwidth free for the whole internet. See `ice.viewer_ice`. The page
        # refreshes this before every attempt via `/whep/<code>/ice`.
        "iceServers": ice.viewer_ice(),
    }
    return _page(body, _SCRIPT, config, title=safe_title)


def render_index() -> str:
    """The relay's root. Deliberately says nothing about who is streaming."""
    body = """
<header><span class='title'>horrible share relay</span></header>
<main>
  <div class='stage'>
    <div class='overlay'>
      <h1>Nothing to see here</h1>
      <p>This is a relay for shared screens. A share link is a short code like
         <code>/k7m2x9qp</code> and is handed out by the person sharing.</p>
    </div>
  </div>
</main>
"""
    return _page(body, "", {})


def render_limited() -> str:
    """What an address that has guessed too often gets instead of a viewer page."""
    body = """
<header><span class='title'>horrible share relay</span></header>
<main>
  <div class='stage'>
    <div class='overlay'>
      <h1>Too many attempts</h1>
      <p>This address has opened too many links that do not exist. Wait a minute,
         then try again.</p>
    </div>
  </div>
</main>
"""
    return _page(body, "", {})
