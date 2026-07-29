/**
 * The pause menu — what Escape opens.
 *
 * Three tabs, because a game that has grown a match server needs the three things
 * every game of its kind has: how it feels (sensitivity), where everyone is
 * (servers and players), and which keys do what (controls).
 *
 * It is a plain DOM overlay rather than anything drawn in WebGL, which is the
 * whole reason it can exist at all: pointer lock is released while it is open, so
 * the mouse is a mouse again and every control here is an ordinary focusable
 * element. That also means the *keyboard* is the shell's again — which is why the
 * rebind rows capture through a focused input (the same trick `ShortcutsPanel`
 * uses) instead of a window listener that would fight the dispatcher.
 *
 * See docs/modules/hassault.mdx.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { browseServers, type BrowseMatch, type BrowsePlayer, type MapSummary } from './api';
import {
  ACTIONS,
  SLOTS,
  boundTo,
  defaultControls,
  isDefaultControls,
  keyLabel,
  setBinding,
  type Bindings,
  type GameAction,
} from './controls';
import type { MatchPeer } from './session';

export type MenuTab = 'settings' | 'servers' | 'controls';

export interface GameMenuProps {
  /** Whether we are in a match, and whether it is ours to invite people into. */
  online: boolean;
  hosting: boolean;
  room: string;
  /** Maps this node can actually load, so an unjoinable row says why. */
  maps: MapSummary[];
  peers: MatchPeer[];
  playerId: string;
  sensitivity: number;
  onSensitivity: (value: number) => void;
  controls: Bindings;
  onControls: (next: Bindings) => void;
  /** Join a match: `host` is empty for one on this node. */
  onJoin: (room: string, map: string, host: string) => void;
  onLeave: () => void;
  onInvite: (friendCode: string) => void;
  onResume: () => void;
}

/** How often the browser tab re-asks while it is the visible tab. */
const BROWSE_INTERVAL_MS = 6000;

export function GameMenu(props: GameMenuProps) {
  const [tab, setTab] = useState<MenuTab>('settings');

  return (
    <div style={styles.backdrop} onClick={(e) => e.stopPropagation()}>
      <div style={styles.sheet}>
        <div style={styles.header}>
          <strong style={{ letterSpacing: '0.14em', fontSize: '0.95rem' }}>PAUSED</strong>
          <div style={styles.tabs}>
            {(['settings', 'servers', 'controls'] as const).map((id) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                style={{ ...styles.tab, ...(tab === id ? styles.tabActive : null) }}
              >
                {id === 'settings' ? 'Settings' : id === 'servers' ? 'Servers' : 'Controls'}
              </button>
            ))}
          </div>
          <button onClick={props.onResume} style={styles.resume}>
            Resume
          </button>
        </div>

        <div style={styles.body}>
          {tab === 'settings' && (
            <SettingsTab value={props.sensitivity} onChange={props.onSensitivity} />
          )}
          {tab === 'servers' && <ServersTab {...props} />}
          {tab === 'controls' && (
            <ControlsTab bindings={props.controls} onChange={props.onControls} />
          )}
        </div>

        <div style={styles.footer}>
          Esc resumes · hold Esc to give the mouse back to the app
          {props.online && (
            <button onClick={props.onLeave} style={styles.leave}>
              Leave match
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- settings ---------------------------------------------------------------

const SENS_MIN = 0.1;
const SENS_MAX = 4;

function SettingsTab({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <div style={styles.rows}>
      <div style={styles.row}>
        <div style={styles.rowMain}>
          <span>Mouse sensitivity</span>
          <span style={styles.dim}>
            Multiplies the turn per pixel of mouse movement. Applies to this game only.
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <input
            type="range"
            min={SENS_MIN}
            max={SENS_MAX}
            step={0.05}
            value={value}
            onChange={(e) => onChange(Number(e.target.value))}
            style={{ width: 180 }}
            aria-label="Mouse sensitivity"
          />
          <code style={{ width: 44, textAlign: 'right' }}>{value.toFixed(2)}×</code>
          <button onClick={() => onChange(1)} disabled={value === 1}>
            Reset
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- servers ----------------------------------------------------------------

function ServersTab(props: GameMenuProps) {
  const [filter, setFilter] = useState('');
  const [matches, setMatches] = useState<BrowseMatch[]>([]);
  const [players, setPlayers] = useState<BrowsePlayer[]>([]);
  const [partial, setPartial] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await browseServers();
      setMatches(data.matches);
      setPlayers(data.players);
      setPartial(data.peers_answered < data.peers_asked);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Polled while this tab is open and nowhere else: the fan-out waits on other
    // people's nodes, so it is far too expensive to leave running behind a game.
    const timer = window.setInterval(() => void refresh(), BROWSE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const known = useMemo(() => new Set(props.maps.map((m) => m.name)), [props.maps]);
  const q = filter.trim().toLowerCase();
  const visibleMatches = matches.filter(
    (m) =>
      q === '' ||
      m.map.toLowerCase().includes(q) ||
      m.hostName.toLowerCase().includes(q) ||
      m.id.toLowerCase().includes(q),
  );
  const visiblePlayers = players.filter((p) => q === '' || p.name.toLowerCase().includes(q));

  return (
    <div>
      <div style={styles.toolbar}>
        <input
          placeholder="Search maps, hosts or players…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ flex: 1 }}
          autoFocus
        />
        <button onClick={() => void refresh()}>Refresh</button>
      </div>

      {error && <div style={styles.error}>{error}</div>}
      {partial && (
        <div style={styles.dim}>
          Some friends' nodes didn't answer in time — this list may be incomplete.
        </div>
      )}

      <h4 style={styles.heading}>
        Matches {loading && matches.length === 0 ? '…' : `(${visibleMatches.length})`}
      </h4>
      {visibleMatches.length === 0 && !loading && (
        <div style={styles.dim}>
          Nothing running. Join a match from the toolbar to start one here — friends on the fabric
          will see it in their own browser.
        </div>
      )}
      {visibleMatches.map((m) => {
        const here = m.host === '';
        const playable = known.has(m.map);
        const current = here && m.id === props.room;
        return (
          <div key={`${m.host}:${m.id}`} style={styles.row}>
            <div style={styles.rowMain}>
              <span>
                <code>{m.map}</code> · {m.hostName}
                {current && <span style={styles.badge}>you're in this one</span>}
              </span>
              <span style={styles.dim}>
                {m.players - m.bots} player{m.players - m.bots === 1 ? '' : 's'}
                {m.bots > 0 ? ` + ${m.bots} bot${m.bots === 1 ? '' : 's'}` : ''} of {m.maxPlayers}
                {' · '}
                <code>{m.id.slice(0, 8)}</code>
                {!playable && ' · map not on this machine'}
              </span>
            </div>
            <button
              onClick={() => props.onJoin(m.id, m.map, m.host)}
              disabled={current || !playable || m.players >= m.maxPlayers}
              title={
                playable
                  ? undefined
                  : 'That map is neither bundled nor in your AssaultCube install, so it cannot be loaded here.'
              }
            >
              {m.players >= m.maxPlayers ? 'Full' : 'Join'}
            </button>
          </div>
        );
      })}

      <h4 style={styles.heading}>Players ({visiblePlayers.length + props.peers.length})</h4>
      {props.peers.map((p) => (
        <div key={p.id} style={styles.row}>
          <div style={styles.rowMain}>
            <span>
              {p.name}
              {p.id === props.playerId ? ' (you)' : ''}
              <span style={styles.badge}>in your match</span>
            </span>
            <span style={styles.dim}>
              {p.bot ? 'bot' : `${Math.round(p.rtt)} ms`} · {p.kills}/{p.deaths}
              {p.alive ? '' : ' · down'}
            </span>
          </div>
        </div>
      ))}
      {visiblePlayers.length === 0 && props.peers.length === 0 && (
        <div style={styles.dim}>
          No friends online. The roster lives in the Social panel — accepting a friend there is what
          makes their matches visible here.
        </div>
      )}
      {visiblePlayers.map((p) => (
        <div key={p.person_id} style={styles.row}>
          <div style={styles.rowMain}>
            <span>{p.name}</span>
            <span style={styles.dim}>
              {p.devices_online} device{p.devices_online === 1 ? '' : 's'} online
              {p.room ? ` · in your match` : ''}
              {p.can_play ? '' : ' · no match support on their build'}
            </span>
          </div>
          <button
            onClick={() => props.onInvite(p.friend_code)}
            disabled={!props.hosting || !p.can_play || p.room !== ''}
            title={
              props.hosting
                ? undefined
                : 'Join a match on this node first — an invite is to a room you are hosting.'
            }
          >
            Invite
          </button>
        </div>
      ))}
    </div>
  );
}

// ---- controls ---------------------------------------------------------------

interface Recording {
  action: GameAction;
  slot: number;
}

function ControlsTab({
  bindings,
  onChange,
}: {
  bindings: Bindings;
  onChange: (next: Bindings) => void;
}) {
  const [recording, setRecording] = useState<Recording | null>(null);
  const [note, setNote] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (recording) inputRef.current?.focus();
  }, [recording]);

  const capture = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!recording) return;
    // Nothing here reaches the shell: this is a focused text input, so the
    // dispatcher's own typing guard already stands aside.
    e.preventDefault();
    e.stopPropagation();
    const code = e.nativeEvent.code;
    if (e.key === 'Escape') {
      setRecording(null);
      setNote('');
      return;
    }
    // A bare modifier is somebody reaching for a real key — keep waiting.
    if (/^(Shift|Control|Alt|Meta)(Left|Right)$/.test(code)) return;
    const previous = boundTo(bindings, code);
    onChange(setBinding(bindings, recording.action, recording.slot, code));
    setNote(
      previous && previous !== recording.action
        ? `${keyLabel(code)} was ${label(previous)} — that one is now unbound.`
        : '',
    );
    setRecording(null);
  };

  const groups = ['Movement', 'Combat', 'View'] as const;

  return (
    <div>
      <div style={styles.toolbar}>
        <span style={styles.dim}>
          These keys work only while this pane has the pointer — they never fire anywhere else in
          the app, and the app's own shortcuts are suppressed while you play.
        </span>
        <button
          onClick={() => {
            onChange(defaultControls());
            setNote('');
          }}
          disabled={isDefaultControls(bindings)}
        >
          Reset all
        </button>
      </div>

      {recording && (
        <div style={styles.recording}>
          Press a key for <strong>{label(recording.action)}</strong> — Esc to cancel.
          {/* Focused so the keystroke lands here rather than doing whatever it is
              currently bound to elsewhere. `readOnly` keeps a caret out of it. */}
          <input
            ref={inputRef}
            readOnly
            value=""
            onKeyDown={capture}
            onBlur={() => setRecording(null)}
            style={styles.recorder}
          />
        </div>
      )}
      {note && <div style={styles.dim}>{note}</div>}

      {groups.map((group) => (
        <div key={group}>
          <h4 style={styles.heading}>{group}</h4>
          {ACTIONS.filter((a) => a.group === group).map((doc) => (
            <div key={doc.action} style={styles.row}>
              <div style={styles.rowMain}>
                <span>{doc.label}</span>
                {doc.note && <span style={styles.dim}>{doc.note}</span>}
              </div>
              <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
                {Array.from({ length: SLOTS }, (_, slot) => {
                  const code = bindings[doc.action]?.[slot];
                  const active = recording?.action === doc.action && recording?.slot === slot;
                  return (
                    <button
                      key={slot}
                      onClick={() => setRecording({ action: doc.action, slot })}
                      style={{
                        ...styles.keycap,
                        ...(active ? styles.keycapActive : null),
                        ...(code ? null : styles.keycapEmpty),
                      }}
                      title={code ? `${code} — click to rebind` : 'Click to bind a key'}
                    >
                      {active ? '…' : code ? keyLabel(code) : '+'}
                    </button>
                  );
                })}
                <button
                  onClick={() => onChange(setBinding(bindings, doc.action, 0, null))}
                  disabled={(bindings[doc.action]?.length ?? 0) === 0}
                  title="Clear the first key"
                  style={styles.clear}
                >
                  ×
                </button>
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function label(action: GameAction): string {
  return ACTIONS.find((a) => a.action === action)?.label ?? action;
}

// ---- styles -----------------------------------------------------------------
//
// Inline, like the rest of this pane: the panel is one self-contained document
// view with no stylesheet of its own, and adding one for a menu that only exists
// over a WebGL canvas would put its appearance two files away from its markup.

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'absolute',
    inset: 0,
    zIndex: 5,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'rgba(8,10,14,0.78)',
    backdropFilter: 'blur(2px)',
  },
  sheet: {
    width: 'min(680px, 92%)',
    maxHeight: '86%',
    display: 'flex',
    flexDirection: 'column',
    background: 'rgba(13,17,23,0.97)',
    border: '1px solid var(--border, #2a2a2a)',
    borderRadius: 8,
    color: 'var(--text)',
    fontSize: '0.82rem',
    boxShadow: '0 18px 50px rgba(0,0,0,0.5)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.6rem',
    padding: '0.6rem 0.8rem',
    borderBottom: '1px solid var(--border, #2a2a2a)',
  },
  tabs: { display: 'flex', gap: '0.25rem', marginLeft: 'auto' },
  tab: {
    background: 'transparent',
    border: '1px solid transparent',
    color: 'var(--text-dim)',
    padding: '0.25rem 0.6rem',
    borderRadius: 5,
    cursor: 'pointer',
  },
  tabActive: {
    background: 'rgba(110,168,254,0.14)',
    borderColor: 'var(--accent, #6ea8fe)',
    color: 'var(--text)',
  },
  resume: { marginLeft: '0.4rem' },
  body: { padding: '0.7rem 0.8rem', overflowY: 'auto', minHeight: 0 },
  footer: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.6rem',
    padding: '0.5rem 0.8rem',
    borderTop: '1px solid var(--border, #2a2a2a)',
    color: 'var(--text-dim)',
    fontSize: '0.74rem',
  },
  leave: { marginLeft: 'auto' },
  rows: { display: 'flex', flexDirection: 'column' },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.8rem',
    padding: '0.4rem 0',
    borderBottom: '1px solid rgba(255,255,255,0.055)',
  },
  rowMain: { display: 'flex', flexDirection: 'column', gap: 2, flex: 1, minWidth: 0 },
  dim: { color: 'var(--text-dim)', fontSize: '0.74rem' },
  heading: {
    margin: '0.9rem 0 0.3rem',
    fontSize: '0.72rem',
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
    color: 'var(--text-dim)',
  },
  toolbar: { display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem' },
  error: { color: '#f85149', padding: '0.3rem 0' },
  badge: {
    marginLeft: '0.4rem',
    fontSize: '0.66rem',
    padding: '0.05rem 0.3rem',
    borderRadius: 3,
    background: 'rgba(110,168,254,0.16)',
    color: 'var(--accent, #6ea8fe)',
  },
  recording: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    padding: '0.4rem 0.5rem',
    borderRadius: 5,
    background: 'rgba(110,168,254,0.12)',
    border: '1px solid var(--accent, #6ea8fe)',
  },
  recorder: { width: 1, opacity: 0, border: 'none', background: 'transparent' },
  keycap: {
    minWidth: 46,
    fontFamily: 'var(--font-mono, monospace)',
    padding: '0.2rem 0.4rem',
    cursor: 'pointer',
  },
  keycapActive: { borderColor: 'var(--accent, #6ea8fe)', color: 'var(--accent, #6ea8fe)' },
  keycapEmpty: { opacity: 0.45 },
  clear: { padding: '0.1rem 0.35rem' },
};
