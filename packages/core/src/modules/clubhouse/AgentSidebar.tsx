/**
 * The voice agent's side panel, and the rail that stands in for it when it is hidden.
 *
 * Extracted from `RoomsPanel.tsx`, where it was ~1,100 lines of inline JSX. It owns no
 * agent state: the config, the status flags and every action come in as props, so the
 * room pane stays the one place that talks to the voice session. What it does own is
 * presentation state (which cards are open, the persona draft, the saved presets).
 *
 * Every control here changes something the agent actually does, in every mode. The
 * settings that only acted under one posture, or did nothing, are gone; see
 * `normalizeVoiceConfig`.
 */
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';

import { dialogs } from '../../dialogs';
import { toastsStore } from '../../toasts';
import type { TtsVoiceOption } from './api';
import { loadPresets, savePresets, upsertPreset, type PersonaPreset } from './agentPresets';
import type { EarsHealth } from './useClubhouseVoice';
import {
  ENDPOINTING_RANGE,
  type VoiceAgentConfig,
  type VoiceEvent,
  type VoiceHealth,
  type VoiceLegHealth,
  type VoiceStateTurn,
} from './voiceAgent';

export interface HeardLine {
  id: number;
  text: string;
  speaker?: string;
  time: string;
}

/** Finished utterances kept on screen. The agent's memory holds the rest. */
export const HEARD_LINES_KEPT = 6;

export type AgentRailState = 'off' | 'idle' | 'thinking' | 'speaking' | 'error';

/**
 * The slice of the dashboard agent's status this panel reads. Declared here, not
 * imported from the agent module, so the panel depends on three fields and not on a
 * sibling module's whole status type (the room pane passes it the real thing).
 */
export interface AgentEngine {
  provider: string | null;
  model: string | null;
  available_models: string[];
}

export interface AgentSidebarProps {
  width: number;
  config: VoiceAgentConfig;
  onPatch: (patch: Partial<VoiceAgentConfig>) => void;
  /** The name the agent answers to: the connected account's display name. */
  agentName: string;
  /** The account is in the audience, so replies can only go to text chat. */
  audienceOnly: boolean;
  speaking: boolean;
  thinking: boolean;
  /** Why the last turn did or did not produce speech. */
  lastReason: string | null;
  health: VoiceHealth | null;
  /** Speech-to-text / text-to-speech failure from the voice session. */
  speechError: string | null;
  /** The last attempt to send this config to the server failed. */
  syncError: string | null;
  heardLines: HeardLine[];
  liveCaption: { text: string; speaker?: string } | null;
  getEarsHealth: () => EarsHealth;
  /** What the agent currently remembers, oldest first. */
  memory: VoiceStateTurn[];
  /** Server and pane events merged, oldest first. */
  events: VoiceEvent[];
  onClearLog: () => void;
  engine: AgentEngine | null;
  ttsVoices: TtsVoiceOption[];
  networkOpen: boolean;
  onToggleNetwork: () => void;
  onApplyPersona: (persona: string, clearMemory: boolean) => void | Promise<void>;
  onClearContext: () => void;
  onInterrupt: () => void;
  onSpeakNow: () => void;
  onTestVoice: () => void;
  onHide: () => void;
}

// --- icons ---------------------------------------------------------------------------
// Stroke icons that take their colour from the surrounding text, so a container sets
// the tone and no glyph needs a colour of its own.

function Icon({ children, size = 16 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

const BotIcon = () => (
  <Icon>
    <rect x="4" y="8" width="16" height="11" rx="2" />
    <path d="M12 8V4M9 13h.01M15 13h.01M9 16h6" />
  </Icon>
);
const CloseIcon = () => (
  <Icon>
    <path d="M6 6l12 12M18 6L6 18" />
  </Icon>
);
const ChevronIcon = () => (
  <Icon size={14}>
    <path d="M9 6l6 6-6 6" />
  </Icon>
);
const CopyIcon = () => (
  <Icon size={14}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V6a2 2 0 0 1 2-2h8" />
  </Icon>
);
const TrashIcon = () => (
  <Icon size={14}>
    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />
  </Icon>
);
const PlayIcon = () => (
  <Icon size={14}>
    <path d="M8 5l11 7-11 7z" />
  </Icon>
);
const StopIcon = () => (
  <Icon size={14}>
    <rect x="6" y="6" width="12" height="12" rx="1" />
  </Icon>
);
const MicIcon = () => (
  <Icon size={14}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
  </Icon>
);

// --- styles --------------------------------------------------------------------------

const AGENT_CSS = `
.ag-root {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex-shrink: 0;
  min-width: 240px;
  background: var(--bg-raised);
  color: var(--text);
  font-size: 0.78rem;
}
.ag-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.6rem 0.8rem;
  background: var(--bg-elevated);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.ag-head-left { display: flex; align-items: center; gap: 0.5rem; min-width: 0; }
.ag-head-right { display: flex; align-items: center; gap: 0.4rem; }
.ag-title {
  font-size: 0.68rem;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--text-strong);
}
.ag-mono {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.64rem;
  color: var(--text-dim);
}
.ag-state {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  padding: 1px 6px;
  border: 1px solid currentColor;
  color: var(--text-faint);
}
.ag-state[data-tone='idle'] { color: var(--accent); }
.ag-state[data-tone='thinking'] { color: var(--warn); }
.ag-state[data-tone='speaking'] { color: var(--success); }
.ag-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0.7rem;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}
.ag-foot {
  display: flex;
  gap: 0.4rem;
  padding: 0.6rem 0.8rem;
  background: var(--bg-elevated);
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}
.ag-foot .ag-btn { flex: 1; justify-content: center; }
.ag-card {
  border-top: 2px solid var(--accent);
  background-color: var(--bg);
  background-image:
    linear-gradient(color-mix(in srgb, var(--text) 4%, transparent) 1px, transparent 1px),
    linear-gradient(90deg, color-mix(in srgb, var(--text) 4%, transparent) 1px, transparent 1px);
  background-size: 16px 16px;
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%);
  animation: ag-card-in 0.22s ease-out both;
  animation-delay: calc(var(--i, 0) * 40ms);
}
@keyframes ag-card-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) { .ag-card { animation: none; } }
.ag-card-head { display: flex; align-items: center; gap: 0.4rem; padding-right: 0.5rem; }
.ag-card-toggle {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.55rem 0.6rem;
  background: transparent;
  border: 0;
  color: inherit;
  cursor: pointer;
  text-align: left;
}
.ag-card-toggle svg { transition: transform 0.15s ease; flex-shrink: 0; color: var(--text-dim); }
.ag-card-toggle[aria-expanded='true'] svg { transform: rotate(90deg); }
.ag-card-meta { margin-left: auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ag-card-body { padding: 0.1rem 0.7rem 0.8rem; display: flex; flex-direction: column; gap: 0.7rem; }
.ag-field { display: flex; flex-direction: column; gap: 0.3rem; min-width: 0; }
.ag-row-fields { display: flex; gap: 0.6rem; flex-wrap: wrap; }
.ag-row-fields > .ag-field { flex: 1; min-width: 110px; }
.ag-label { font-size: 0.7rem; font-weight: 600; color: var(--text-dim); }
.ag-hint { font-size: 0.66rem; line-height: 1.35; color: var(--text-faint); margin: 0; }
.ag-input {
  width: 100%;
  padding: 0 0.6rem;
  font-size: 0.8rem;
  background: var(--bg-inset);
  color: var(--text-strong);
  border: 1px solid var(--border);
  border-radius: 0;
}
.ag-range { width: 100%; accent-color: var(--accent); padding: 0; }
.ag-textarea {
  width: 100%;
  min-height: 84px;
  padding: 0.55rem 0.6rem;
  font-size: 0.78rem;
  background: var(--bg-inset);
  color: var(--text-strong);
  border: 1px solid var(--border);
  border-radius: 0;
  resize: vertical;
}
.ag-check { display: flex; align-items: flex-start; gap: 0.5rem; cursor: pointer; }
.ag-check input { margin-top: 2px; accent-color: var(--accent); }
.ag-check-text { display: flex; flex-direction: column; gap: 1px; }
.ag-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  min-height: 28px;
  padding: 0 0.7rem;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  clip-path: polygon(0 0, calc(100% - 7px) 0, 100% 7px, 100% 100%, 0 100%);
  cursor: pointer;
}
.ag-btn:hover:not(:disabled) { background: var(--bg-hover); }
.ag-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.ag-btn[data-tone='primary'] { color: var(--accent-contrast); background: var(--accent); border-color: var(--accent); }
.ag-btn[data-tone='primary']:hover:not(:disabled) { background: var(--accent); filter: brightness(1.1); }
.ag-btn[data-tone='danger'] { color: var(--accent-contrast); background: var(--danger); border-color: var(--danger); }
.ag-btn[data-tone='danger']:hover:not(:disabled) { background: var(--danger); filter: brightness(1.1); }
.ag-btn[data-on='true'] { color: var(--success); border-color: var(--success); }
.ag-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  color: var(--text-dim);
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
}
.ag-icon-btn:hover:not(:disabled) { color: var(--text-strong); background: var(--bg-hover); }
.ag-icon-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.ag-chips { display: flex; flex-wrap: wrap; gap: 0.35rem; }
.ag-chip {
  min-height: 24px;
  padding: 0 0.55rem;
  font-size: 0.68rem;
  color: var(--text);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  cursor: pointer;
}
.ag-chip:hover { background: var(--bg-hover); }
.ag-chip[aria-pressed='true'] {
  color: var(--text-strong);
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 18%, transparent);
}
.ag-actions { display: flex; gap: 0.4rem; justify-content: flex-end; flex-wrap: wrap; }
.ag-notice {
  padding: 0.6rem 0.7rem;
  border-left: 2px solid var(--warn);
  background: color-mix(in srgb, var(--warn) 8%, transparent);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}
.ag-notice[data-tone='info'] {
  border-left-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 7%, transparent);
}
.ag-status { display: flex; flex-direction: column; gap: 0.35rem; }
.ag-status-line { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.ag-warn { color: var(--warn); font-size: 0.72rem; }
.ag-ears {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0.45rem 0.6rem;
  background: var(--bg-inset);
  border-top: 2px solid var(--success);
}
.ag-log { display: flex; flex-direction: column; max-height: 260px; overflow-y: auto; }
.ag-log-row {
  display: grid;
  grid-template-columns: auto auto 1fr;
  gap: 0.5rem;
  align-items: baseline;
  padding: 3px 0;
  border-bottom: 1px solid var(--border);
}
.ag-kind {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.58rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--text-faint);
}
.ag-log-row[data-kind='replied'] .ag-kind { color: var(--success); }
.ag-log-row[data-kind='error'] .ag-kind { color: var(--danger); }
.ag-log-row[data-kind='silent'] .ag-kind { color: var(--warn); }
.ag-log-row[data-kind='command'] .ag-kind,
.ag-log-row[data-kind='pane'] .ag-kind { color: var(--accent); }
.ag-log-text { min-width: 0; overflow-wrap: anywhere; }
.ag-log-meta { display: block; }
.ag-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  width: 32px;
  flex-shrink: 0;
  padding: 0.6rem 0;
  background: var(--bg-elevated);
  border: 0;
  border-left: 1px solid var(--border);
  color: var(--text-dim);
  cursor: pointer;
}
.ag-rail:hover { color: var(--text-strong); background: var(--bg-hover); }
.ag-rail-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-faint); }
.ag-rail-dot[data-state='idle'] { background: var(--accent); }
.ag-rail-dot[data-state='thinking'] { background: var(--warn); }
.ag-rail-dot[data-state='speaking'] { background: var(--success); }
.ag-rail-dot[data-state='error'] { background: var(--danger); }
.ag-rail-text {
  writing-mode: vertical-rl;
  font-size: 0.6rem;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}
.ag-root button:focus-visible,
.ag-root select:focus-visible,
.ag-root input:focus-visible,
.ag-root textarea:focus-visible,
.ag-rail:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
`;

// --- small pieces --------------------------------------------------------------------

const CARD_STATE_KEY = 'clubhouseAgentCards';

function readCardOpen(id: string): boolean | undefined {
  try {
    const raw = localStorage.getItem(CARD_STATE_KEY);
    if (!raw) return undefined;
    const value: unknown = JSON.parse(raw)?.[id];
    return typeof value === 'boolean' ? value : undefined;
  } catch {
    return undefined;
  }
}

function writeCardOpen(id: string, open: boolean): void {
  try {
    const raw = localStorage.getItem(CARD_STATE_KEY);
    const map = raw ? JSON.parse(raw) : {};
    localStorage.setItem(CARD_STATE_KEY, JSON.stringify({ ...map, [id]: open }));
  } catch {
    /* storage unavailable: the card simply reopens next time */
  }
}

/**
 * A titled, collapsible section. Whether it is open is remembered per card, so a panel
 * arranged once stays arranged. The body is unmounted while closed, which also stops
 * anything inside it that polls (the ears readout, the log).
 */
function AgentCard({
  id,
  index,
  title,
  meta,
  defaultOpen = true,
  actions,
  children,
}: {
  id: string;
  index: number;
  title: string;
  meta?: string;
  defaultOpen?: boolean;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(() => readCardOpen(id) ?? defaultOpen);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    writeCardOpen(id, next);
  };
  const bodyId = `ag-card-${id}`;
  return (
    <section className="ag-card" style={{ '--i': Math.min(index, 6) } as CSSProperties}>
      <header className="ag-card-head">
        <button
          type="button"
          className="ag-card-toggle"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={toggle}
        >
          <ChevronIcon />
          <span className="ag-title">{title}</span>
          {meta && <span className="ag-mono ag-card-meta">{meta}</span>}
        </button>
        {actions}
      </header>
      {open && (
        <div id={bodyId} className="ag-card-body">
          {children}
        </div>
      )}
    </section>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="ag-field">
      <span className="ag-label">{label}</span>
      {children}
      {hint && <p className="ag-hint">{hint}</p>}
    </label>
  );
}

function Check({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <label className="ag-check">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="ag-check-text">
        <span>{label}</span>
        {hint && <span className="ag-hint">{hint}</span>}
      </span>
    </label>
  );
}

/**
 * A number box that lets you clear it. Bound straight to a number, an emptied box
 * becomes 0 on the next render and can never be retyped; this keeps the text locally
 * and only commits a value that parses, then snaps back to the stored one on blur.
 */
function NumberField({
  value,
  min,
  max,
  step,
  onCommit,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onCommit: (next: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return (
    <input
      type="number"
      className="ag-input"
      min={min}
      max={max}
      step={step}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        const n = e.target.value.trim() === '' ? NaN : Number(e.target.value);
        if (Number.isFinite(n)) onCommit(n);
      }}
      onBlur={() => setText(String(value))}
    />
  );
}

function TranscriptLines({
  lines,
  live,
}: {
  lines: HeardLine[];
  live: { text: string; speaker?: string } | null;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
      {lines.map((line, i) => (
        <div
          key={line.id}
          style={{
            display: 'flex',
            gap: '0.45rem',
            alignItems: 'baseline',
            // Older lines recede, so the eye lands on the newest.
            opacity: 0.55 + (0.45 * (i + 1)) / lines.length,
          }}
        >
          <span className="ag-mono" style={{ flexShrink: 0 }}>
            {line.time}
          </span>
          <span>
            <strong style={{ color: 'var(--accent)' }}>{line.speaker || 'Room'}:</strong>{' '}
            {line.text}
          </span>
        </div>
      ))}
      {live && (
        <div
          style={{
            display: 'flex',
            gap: '0.45rem',
            alignItems: 'baseline',
            color: 'var(--text-dim)',
            fontStyle: 'italic',
          }}
        >
          <span className="ag-mono" style={{ color: 'var(--success)', fontWeight: 700 }}>
            LIVE
          </span>
          <span>
            <strong style={{ color: 'var(--accent)', fontStyle: 'normal' }}>
              {live.speaker || 'Room'}:
            </strong>{' '}
            {live.text}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * What the agent's ears are doing, under the transcript.
 *
 * "Listening…" used to mean three different things: the room is quiet, nobody's audio
 * ever reached the agent, or the level never crosses the threshold. So this shows the
 * voice detector's own level with the line it has to cross drawn on it, plus the things
 * that are simply true or false: how many remote tracks are wired in, whether the
 * audio tap exists, whether the context is running. Polled at 4 Hz — the detector
 * writes fifty times a second and pushing that through React would re-render at 50 Hz.
 */
function EarsReadout({ getHealth, hasHeard }: { getHealth: () => EarsHealth; hasHeard: boolean }) {
  const [h, setH] = useState<EarsHealth>(getHealth);
  useEffect(() => {
    const interval = setInterval(() => {
      const next = getHealth();
      // A fresh object every call: keep the old one when every reading matches,
      // so a quiet room does not re-render four times a second.
      setH((prev) =>
        (Object.keys(next) as (keyof EarsHealth)[]).every((k) => prev[k] === next[k]) ? prev : next,
      );
    }, 250);
    return () => clearInterval(interval);
  }, [getHealth]);

  // The one reading that is always wrong, in priority order. Nothing wired in beats
  // a dead recorder beats a suspended context: each makes the next unknowable.
  const fault =
    h.tracksConnected === 0
      ? 'No room audio is reaching the agent — nobody is publishing, or the subscribe failed.'
      : h.recorderState === null
        ? 'The audio tap is down; rebuilding it.'
        : h.contextState !== 'running'
          ? `Audio is ${h.contextState ?? 'unavailable'} — the browser suspended it.`
          : null;

  const pct = Math.min(100, (h.level / 40) * 100);
  const thresholdPct = Math.min(100, (h.speechLevel / 40) * 100);
  const over = h.level > h.speechLevel;
  const secsSinceHeard = h.lastHeardAt ? Math.round((Date.now() - h.lastHeardAt) / 1000) : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '2px' }}>
      {!hasHeard && !fault && (
        <span style={{ color: 'var(--text-dim)', fontStyle: 'italic', fontSize: '0.7rem' }}>
          {over
            ? 'Hearing the room — transcribing…'
            : 'Listening. Nothing above the speech line yet.'}
        </span>
      )}
      {fault && (
        <span style={{ color: 'var(--danger)', fontSize: '0.7rem', fontWeight: 600 }}>{fault}</span>
      )}
      <div
        style={{
          position: 'relative',
          height: '6px',
          background: 'var(--bg-inset)',
          overflow: 'hidden',
        }}
        title={`Input level ${h.level.toFixed(1)} of the ${h.speechLevel} needed to count as speech`}
      >
        <div
          style={{
            width: '100%',
            height: '100%',
            background: over ? 'var(--success)' : 'var(--text-faint)',
            // Scaled, not resized: the meter moves ~4 times a second and a width
            // transition re-lays out the panel on every one of them.
            transform: `scaleX(${pct / 100})`,
            transformOrigin: 'left',
            transition: 'transform 120ms linear',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: `${thresholdPct}%`,
            top: 0,
            bottom: 0,
            width: '2px',
            background: 'var(--text)',
            opacity: 0.55,
          }}
        />
      </div>
      <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }} className="ag-mono">
        <span>
          lvl {h.level.toFixed(1)}/{h.speechLevel}
        </span>
        <span>
          {h.tracksConnected} track{h.tracksConnected === 1 ? '' : 's'}
        </span>
        <span>tap {h.recorderState ? 'live' : 'down'}</span>
        {h.restarts > 0 && (
          <span title="Times the watchdog rebuilt the audio tap">restarts {h.restarts}</span>
        )}
        <span>
          {secsSinceHeard === null ? 'no transcript yet' : `heard ${secsSinceHeard}s ago`}
        </span>
      </div>
    </div>
  );
}

/**
 * The three legs of a voice turn, as one pill: READY when all work, otherwise one
 * pill per leg that is down, carrying its fix in the tooltip. A leg whose probe is
 * `certain: false` renders as unknown, never as broken — "we asked and it is absent"
 * and "we could not ask" are different facts.
 */
function VoiceHealthChips({ health }: { health: VoiceHealth }) {
  const legs: [string, VoiceLegHealth][] = [
    ['HEARS', health.ears],
    ['THINKS', health.model],
    ['SPEAKS', health.mouth],
  ];
  const bad = legs.filter(([, leg]) => !leg.ok);

  const pill = (label: string, tone: 'ok' | 'bad' | 'unknown', title: string) => {
    const color = { ok: 'var(--success)', bad: 'var(--danger)', unknown: 'var(--text-dim)' }[tone];
    return (
      <span
        key={label}
        title={title}
        className="ag-mono"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.35rem',
          padding: '0 0.5rem',
          color,
          border: `1px solid ${color}`,
          fontWeight: 700,
          letterSpacing: '0.08em',
        }}
      >
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: color }} />
        {label}
      </span>
    );
  };

  if (bad.length === 0) {
    return pill(
      'READY',
      'ok',
      `${health.provider || 'model'} · ${health.model_name || 'default'} @ ${health.endpoint}`,
    );
  }
  return (
    <span style={{ display: 'inline-flex', gap: '0.35rem', flexWrap: 'wrap' }}>
      {bad.map(([label, leg]) =>
        pill(
          `NO ${label}`,
          leg.certain ? 'bad' : 'unknown',
          [leg.detail, leg.fix && `Fix: ${leg.fix}`].filter(Boolean).join(' — '),
        ),
      )}
    </span>
  );
}

// --- the log -------------------------------------------------------------------------

const KIND_LABEL: Record<VoiceEvent['kind'], string> = {
  skipped: 'SKIPPED',
  replied: 'REPLIED',
  silent: 'DROPPED',
  command: 'COMMAND',
  error: 'ERROR',
  pane: 'PANE',
};

function eventTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
}

/** One event as a plain line, for the log view and for Copy. */
function eventSummary(e: VoiceEvent): { text: string; meta: string } {
  const who = e.speaker || (e.source === 'chat' ? 'Chat' : 'Room');
  const meta = [e.ms != null ? `${e.ms} ms` : '', e.model, e.detail].filter(Boolean).join(' · ');
  switch (e.kind) {
    case 'skipped':
      return { text: `${who}: “${e.text}” — ${e.reason}`, meta };
    case 'replied':
      return { text: `“${e.text}”`, meta };
    case 'silent':
    case 'error':
      return { text: e.reason, meta };
    case 'command':
      return { text: e.reason, meta };
    default:
      return { text: e.text || e.reason, meta };
  }
}

function LogCard({
  events,
  onClear,
  index,
}: {
  events: VoiceEvent[];
  onClear: () => void;
  index: number;
}) {
  const [errorsOnly, setErrorsOnly] = useState(false);
  const shown = (errorsOnly ? events.filter((e) => e.kind === 'error') : events).slice(-80);
  const newestFirst = [...shown].reverse();

  const copy = async () => {
    const text = shown
      .map((e) => {
        const { text: line, meta } = eventSummary(e);
        return `[${eventTime(e.ts)}] ${KIND_LABEL[e.kind]} ${line}${meta ? ` (${meta})` : ''}`;
      })
      .join('\n');
    try {
      await navigator.clipboard.writeText(text);
      toastsStore.add('success', 'Agent log', `Copied ${shown.length} line(s).`);
    } catch {
      toastsStore.add('warning', 'Agent log', 'Could not copy to the clipboard.');
    }
  };

  return (
    <AgentCard
      id="log"
      index={index}
      title="Debug log"
      meta={`${events.length} event${events.length === 1 ? '' : 's'}`}
      actions={
        <>
          <button
            type="button"
            className="ag-icon-btn"
            onClick={() => void copy()}
            disabled={shown.length === 0}
            title="Copy the log as text"
            aria-label="Copy the log"
          >
            <CopyIcon />
          </button>
          <button
            type="button"
            className="ag-icon-btn"
            onClick={onClear}
            disabled={events.length === 0}
            title="Clear the pane's view of the log"
            aria-label="Clear the log"
          >
            <TrashIcon />
          </button>
        </>
      }
    >
      <Check checked={errorsOnly} onChange={setErrorsOnly} label="Errors only" />
      {newestFirst.length === 0 ? (
        <p className="ag-hint">
          {events.length === 0
            ? 'Nothing yet. Every line the agent hears and every decision it makes lands here.'
            : 'No errors.'}
        </p>
      ) : (
        <div className="ag-log" role="log" aria-live="polite">
          {newestFirst.map((e, i) => {
            const { text, meta } = eventSummary(e);
            return (
              <div className="ag-log-row" data-kind={e.kind} key={`${e.ts}-${i}`}>
                <span className="ag-mono">{eventTime(e.ts)}</span>
                <span className="ag-kind">{KIND_LABEL[e.kind]}</span>
                <span className="ag-log-text">
                  {text}
                  {meta && <span className="ag-mono ag-log-meta">{meta}</span>}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </AgentCard>
  );
}

// --- persona -------------------------------------------------------------------------

function PersonaBody({
  persona,
  onPatch,
  onApplyPersona,
}: {
  persona: string;
  onPatch: (patch: Partial<VoiceAgentConfig>) => void;
  onApplyPersona: AgentSidebarProps['onApplyPersona'];
}) {
  const [presets, setPresets] = useState<PersonaPreset[]>(loadPresets);
  const [draft, setDraft] = useState(persona);
  // The persona can change under us (a preset applied, Reset). Follow it.
  useEffect(() => setDraft(persona), [persona]);
  const active = presets.find((p) => p.prompt === draft);

  const savePreset = async () => {
    const name = await dialogs.prompt({
      title: 'Save persona preset',
      message: 'Give this persona a name so you can pick it again later.',
      placeholder: 'e.g. Late-night host',
      confirmLabel: 'Save',
      defaultValue: active && !active.builtin ? active.name : '',
    });
    if (!name || !name.trim()) return;
    const next = upsertPreset(presets, name, draft);
    setPresets(next);
    savePresets(next);
    toastsStore.add('success', 'Preset saved', `Saved “${name.trim()}”.`);
  };

  const deletePreset = async () => {
    if (!active || active.builtin) return;
    const ok = await dialogs.confirm({
      title: 'Delete preset?',
      message: `“${active.name}” will be removed from your presets.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    const next = presets.filter((p) => p !== active);
    setPresets(next);
    savePresets(next);
  };

  return (
    <>
      <div className="ag-chips" role="group" aria-label="Persona presets">
        {presets.map((p) => (
          <button
            key={p.name}
            type="button"
            className="ag-chip"
            aria-pressed={active === p}
            title={p.prompt}
            onClick={() => {
              setDraft(p.prompt);
              void onApplyPersona(p.prompt, true);
            }}
          >
            {p.chip ?? p.name}
          </button>
        ))}
      </div>
      <label className="ag-field">
        <span className="ag-label">
          Persona <span className="ag-mono">{draft.length} chars</span>
        </span>
        <textarea
          className="ag-textarea"
          value={draft}
          placeholder="Who the agent is and how it talks. The speech rules are always added."
          onChange={(e) => {
            setDraft(e.target.value);
            onPatch({ persona: e.target.value });
          }}
        />
      </label>
      <div className="ag-actions">
        <button
          type="button"
          className="ag-btn"
          onClick={() => {
            setDraft('');
            void onApplyPersona('', true);
          }}
          title="Restore the default persona and clear the agent's memory"
        >
          Reset
        </button>
        {active && !active.builtin && (
          <button type="button" className="ag-btn" onClick={() => void deletePreset()}>
            <TrashIcon /> Delete preset
          </button>
        )}
        <button
          type="button"
          className="ag-btn"
          onClick={() => void savePreset()}
          disabled={!draft.trim()}
        >
          Save as preset
        </button>
        <button
          type="button"
          className="ag-btn"
          onClick={() => void onApplyPersona(draft, false)}
          title="Use this persona from the next turn, keeping what the agent remembers"
        >
          Apply
        </button>
        <button
          type="button"
          className="ag-btn"
          data-tone="primary"
          onClick={() => void onApplyPersona(draft, true)}
          title="Use this persona and clear the agent's memory, so it switches at once"
        >
          Apply and forget
        </button>
      </div>
    </>
  );
}

// --- voice ---------------------------------------------------------------------------

const FALLBACK_VOICES: [string, string][] = [
  ['en-US-AndrewMultilingualNeural', 'Andrew (US male, warm)'],
  ['en-US-AvaMultilingualNeural', 'Ava (US female, conversational)'],
  ['en-US-BrianMultilingualNeural', 'Brian (US male, friendly)'],
  ['en-US-EmmaMultilingualNeural', 'Emma (US female, cheerful)'],
  ['en-US-ChristopherNeural', 'Christopher (US male, authoritative)'],
  ['en-US-JennyNeural', 'Jenny (US female, clear)'],
  ['en-US-GuyNeural', 'Guy (US male, casual)'],
  ['en-US-AriaNeural', 'Aria (US female, expressive)'],
  ['en-US-EricNeural', 'Eric (US male, crisp)'],
  ['en-GB-RyanNeural', 'Ryan (UK male)'],
  ['en-GB-SoniaNeural', 'Sonia (UK female)'],
  ['en-AU-NatNeural', 'Nat (AU female)'],
  ['en-AU-WilliamNeural', 'William (AU male)'],
  ['en-IN-NeerjaNeural', 'Neerja (IN female)'],
  ['ja-JP-KeitaNeural', 'Keita (Japanese male)'],
  ['es-ES-AlvaroNeural', 'Alvaro (Spanish male)'],
  ['fr-FR-HenriNeural', 'Henri (French male)'],
  ['de-DE-ConradNeural', 'Conrad (German male)'],
];

const POSTURE_HELP: Record<VoiceAgentConfig['posture'], (name: string) => string> = {
  addressed: (name) => `Answers only when someone says “${name}”.`,
  conversational: (name) =>
    `Answers when someone says “${name}”, and also joins in on its own, waiting between replies.`,
  always: () => 'Answers every line it hears, with no waiting. Chatty.',
};

// --- the sidebar ---------------------------------------------------------------------

export function AgentSidebar(props: AgentSidebarProps) {
  const { config, onPatch, engine } = props;
  const name = props.agentName || 'the agent';
  const state: AgentRailState = !config.enabled
    ? 'off'
    : props.speaking
      ? 'speaking'
      : props.thinking
        ? 'thinking'
        : 'idle';
  const stateLabel = {
    off: 'OFF',
    idle: 'LISTENING',
    thinking: 'THINKING',
    speaking: 'SPEAKING',
    error: 'ERROR',
  }[state];

  const voices: [string, string][] =
    props.ttsVoices.length > 0
      ? props.ttsVoices.map((v) => [v.name, v.label || v.name])
      : FALLBACK_VOICES;
  const voiceKnown = voices.some(([value]) => value === config.ttsVoice);

  const models = engine?.available_models ?? [];
  const modelKnown = !config.model || models.includes(config.model);
  const defaultModelLabel = `Dashboard default${engine?.model ? ` (${engine.model})` : ''}`;

  return (
    <aside className="ag-root" style={{ width: props.width }} aria-label="Voice agent">
      <style>{AGENT_CSS}</style>

      <div className="ag-head">
        <div className="ag-head-left">
          <BotIcon />
          <span className="ag-title">Voice agent</span>
          <span className="ag-state" data-tone={state === 'off' ? undefined : state}>
            {stateLabel}
          </span>
        </div>
        <div className="ag-head-right">
          <button
            type="button"
            className="ag-btn"
            data-on={config.enabled}
            aria-pressed={config.enabled}
            onClick={() => onPatch({ enabled: !config.enabled })}
            title={config.enabled ? 'Turn the agent off in this room' : 'Turn the agent on'}
          >
            {config.enabled ? 'On' : 'Off'}
          </button>
          <button
            type="button"
            className="ag-icon-btn"
            onClick={props.onHide}
            title="Hide this panel"
            aria-label="Hide the agent panel"
          >
            <CloseIcon />
          </button>
        </div>
      </div>

      <div className="ag-body">
        {!config.enabled && (
          <div className="ag-notice">
            <span>The agent is off. It is not listening or answering in this room.</span>
            <button
              type="button"
              className="ag-btn"
              data-tone="primary"
              onClick={() => onPatch({ enabled: true })}
            >
              Turn on
            </button>
          </div>
        )}

        <div className="ag-status">
          <div className="ag-status-line">
            <span className="ag-label">Last turn</span>
            <span className="ag-mono" style={{ color: 'var(--text-strong)' }}>
              {props.lastReason ?? '—'}
            </span>
            {config.enabled && props.health && <VoiceHealthChips health={props.health} />}
          </div>
          {props.speechError && <span className="ag-warn">{props.speechError}</span>}
          {props.syncError && (
            <span className="ag-warn">
              These settings have not reached the server: {props.syncError}
            </span>
          )}
        </div>

        <AgentCard
          id="persona"
          index={0}
          title="Persona"
          meta={config.persona ? 'custom' : 'default'}
        >
          <PersonaBody
            persona={config.persona}
            onPatch={onPatch}
            onApplyPersona={props.onApplyPersona}
          />
        </AgentCard>

        <AgentCard id="answers" index={1} title="When it answers" meta={`as ${name}`}>
          <Field label="Mode" hint={POSTURE_HELP[config.posture](name)}>
            <select
              className="ag-input"
              value={config.posture}
              onChange={(e) => onPatch({ posture: e.target.value as VoiceAgentConfig['posture'] })}
            >
              <option value="addressed">When called by name</option>
              <option value="conversational">Join the conversation</option>
              <option value="always">Reply to everything</option>
            </select>
          </Field>
          {config.posture === 'conversational' && (
            <Field
              label="Wait between replies (seconds)"
              hint="Unprompted replies are spaced at least this far apart. Being called by name always gets an answer."
            >
              <NumberField
                value={config.cooldownS}
                min={0}
                max={120}
                step={1}
                onCommit={(n) => onPatch({ cooldownS: n })}
              />
            </Field>
          )}
          <Check
            checked={config.respondToVoice}
            onChange={(v) => onPatch({ respondToVoice: v })}
            label="Answer what it hears on stage"
          />
          <Check
            checked={config.respondToChat}
            onChange={(v) => onPatch({ respondToChat: v })}
            label="Answer the room chat"
          />
          <Check
            checked={config.speak}
            onChange={(v) => onPatch({ speak: v })}
            label="Say replies out loud"
            hint={
              props.audienceOnly ? 'You are in the audience, so nobody can hear it.' : undefined
            }
          />
          <Check
            checked={config.postToChat}
            onChange={(v) => onPatch({ postToChat: v })}
            label="Post replies to the chat"
            hint={props.audienceOnly ? 'You are in the audience: replies go here only.' : undefined}
          />
          <Check
            checked={config.robotEmojiPrefix}
            onChange={(v) => onPatch({ robotEmojiPrefix: v })}
            label="Start chat posts with a robot emoji"
            hint="So people can tell the agent from you."
          />
        </AgentCard>

        <AgentCard id="hearing" index={2} title="Hearing">
          <div className="ag-ears">
            <span
              className="ag-label"
              style={{ letterSpacing: '0.14em', textTransform: 'uppercase', fontSize: '0.6rem' }}
            >
              Live transcript
            </span>
            <TranscriptLines lines={props.heardLines} live={props.liveCaption} />
            <EarsReadout
              getHealth={props.getEarsHealth}
              hasHeard={props.heardLines.length > 0 || !!props.liveCaption}
            />
          </div>
          <Field
            label={`Wait after someone stops talking: ${(config.endpointingDelayMs / 1000).toFixed(2)} s`}
            hint="How long a pause counts as the end of a sentence. Shorter answers sooner but may cut people off mid-thought."
          >
            <input
              type="range"
              className="ag-range"
              min={ENDPOINTING_RANGE.min}
              max={ENDPOINTING_RANGE.max}
              step={ENDPOINTING_RANGE.step}
              value={config.endpointingDelayMs}
              onChange={(e) => onPatch({ endpointingDelayMs: Number(e.target.value) })}
            />
          </Field>
          <Check
            checked={config.allowBargeIn}
            onChange={(v) => onPatch({ allowBargeIn: v })}
            label="Stop talking when someone starts"
            hint="Off: the agent finishes its sentence even if someone talks over it."
          />
        </AgentCard>

        <AgentCard
          id="voice"
          index={3}
          title="Voice"
          defaultOpen={false}
          meta={config.ttsVoice}
          actions={
            <button
              type="button"
              className="ag-btn"
              onClick={props.onTestVoice}
              title="Hear the current voice, speed and pitch on your speakers"
            >
              <PlayIcon /> Test
            </button>
          }
        >
          <Field label="Voice">
            <select
              className="ag-input"
              value={config.ttsVoice}
              onChange={(e) => onPatch({ ttsVoice: e.target.value })}
            >
              {!voiceKnown && <option value={config.ttsVoice}>{config.ttsVoice}</option>}
              {voices.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <div className="ag-row-fields">
            <Field label="Volume">
              <select
                className="ag-input"
                value={config.ttsVolume}
                onChange={(e) => onPatch({ ttsVolume: e.target.value })}
              >
                <option value="-50%">50%</option>
                <option value="-25%">75%</option>
                <option value="+0%">100%</option>
                <option value="+25%">125%</option>
                <option value="+50%">150%</option>
              </select>
            </Field>
            <Field label="Speed">
              <select
                className="ag-input"
                value={config.ttsRate}
                onChange={(e) => onPatch({ ttsRate: e.target.value })}
              >
                <option value="-20%">0.8x</option>
                <option value="-10%">0.9x</option>
                <option value="+0%">1.0x</option>
                <option value="+10%">1.1x</option>
                <option value="+20%">1.2x</option>
                <option value="+30%">1.3x</option>
              </select>
            </Field>
            <Field label="Pitch">
              <select
                className="ag-input"
                value={config.ttsPitch}
                onChange={(e) => onPatch({ ttsPitch: e.target.value })}
              >
                <option value="-10Hz">-10 Hz</option>
                <option value="-5Hz">-5 Hz</option>
                <option value="+0Hz">Default</option>
                <option value="+5Hz">+5 Hz</option>
                <option value="+10Hz">+10 Hz</option>
              </select>
            </Field>
          </div>
        </AgentCard>

        <AgentCard
          id="model"
          index={4}
          title="Model"
          defaultOpen={false}
          meta={config.model || engine?.model || 'default'}
        >
          <Field
            label="Model for this room"
            hint={
              engine?.provider
                ? `Served by ${engine.provider}. A small, fast model answers sooner; this setting is only for this agent, not the dashboard's own.`
                : undefined
            }
          >
            <select
              className="ag-input"
              value={config.model}
              onChange={(e) => onPatch({ model: e.target.value })}
            >
              <option value="">{defaultModelLabel}</option>
              {!modelKnown && <option value={config.model}>{config.model}</option>}
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </Field>
          <div className="ag-row-fields">
            <Field label="Creativity" hint="0 is steady, higher is looser.">
              <NumberField
                value={config.temperature}
                min={0}
                max={2}
                step={0.1}
                onCommit={(n) => onPatch({ temperature: n })}
              />
            </Field>
            <Field label="Max reply tokens" hint="Caps how long it can talk.">
              <NumberField
                value={config.maxTokens}
                min={20}
                max={1000}
                step={10}
                onCommit={(n) => onPatch({ maxTokens: n })}
              />
            </Field>
            <Field label="Remembers turns" hint="0 forgets each line at once.">
              <NumberField
                value={config.memoryTurns}
                min={0}
                max={40}
                step={2}
                onCommit={(n) => onPatch({ memoryTurns: n })}
              />
            </Field>
          </div>
          {props.memory.length > 0 && (
            <details>
              <summary className="ag-label" style={{ cursor: 'pointer' }}>
                What it remembers ({props.memory.length})
              </summary>
              <div
                style={{
                  maxHeight: 160,
                  overflowY: 'auto',
                  marginTop: '0.4rem',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.2rem',
                }}
              >
                {props.memory.map((t, i) => (
                  <div
                    key={`${t.ts}-${i}`}
                    style={{ color: t.role === 'agent' ? 'var(--accent)' : 'var(--text)' }}
                  >
                    <strong>{t.role === 'agent' ? 'Agent' : t.speaker || 'Room'}:</strong> {t.text}
                  </div>
                ))}
              </div>
            </details>
          )}
        </AgentCard>

        <LogCard index={5} events={props.events} onClear={props.onClearLog} />

        <div className="ag-notice" data-tone="info">
          <span>
            <span className="ag-title">Media telemetry</span>
            <br />
            <span className="ag-mono">UDP/Opus · PubNub WSS · Agora RTN</span>
          </span>
          <button
            type="button"
            className="ag-btn"
            aria-pressed={props.networkOpen}
            onClick={props.onToggleNetwork}
          >
            Inspect
          </button>
        </div>
      </div>

      <div className="ag-foot">
        <button
          type="button"
          className="ag-btn"
          data-tone="danger"
          onClick={props.onInterrupt}
          disabled={!props.speaking && !props.thinking}
          title="Stop speaking now and abandon any reply being written"
        >
          <StopIcon /> Interrupt
        </button>
        <button
          type="button"
          className="ag-btn"
          onClick={props.onSpeakNow}
          disabled={!config.enabled || props.speaking || props.thinking}
          title="Ask the agent to take the floor now"
        >
          <MicIcon /> {props.thinking ? 'Thinking' : props.speaking ? 'Speaking' : 'Speak now'}
        </button>
        <button
          type="button"
          className="ag-btn"
          onClick={props.onClearContext}
          title="Wipe what the agent remembers of this room"
        >
          <TrashIcon /> Forget
        </button>
      </div>
    </aside>
  );
}

/**
 * What stands in for the panel while it is hidden: a thin strip that reopens it, with
 * a dot for what the agent is doing so a hidden panel is not a silent one.
 */
export function AgentRail({ state, onShow }: { state: AgentRailState; onShow: () => void }) {
  return (
    <button
      type="button"
      className="ag-rail"
      onClick={onShow}
      title="Show the voice agent panel"
      aria-label={`Show the voice agent panel (${state})`}
    >
      <style>{AGENT_CSS}</style>
      <BotIcon />
      <span className="ag-rail-dot" data-state={state} />
      <span className="ag-rail-text">Voice agent</span>
    </button>
  );
}
