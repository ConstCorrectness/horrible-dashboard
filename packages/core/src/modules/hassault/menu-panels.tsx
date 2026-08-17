/**
 * The panels the main menu and the pause menu both show.
 *
 * They exist as one file because they are one thing. A game's settings screen and
 * its *paused* settings screen differ only in what surrounds them, and two copies
 * would drift — one gaining a slider the other never got. So the panels live here
 * and the two menus compose them: `MainMenu` in front of the orbiting map,
 * `GameMenu` over the frozen world.
 *
 * Styling is inline, like the rest of this pane: it is one self-contained document
 * view with no stylesheet of its own, and adding one for menus that only exist over
 * a WebGL canvas would put their appearance two files away from their markup.
 *
 * See docs/modules/hassault.mdx.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { setSetting, useSetting } from '../../settings';
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
import type { MatchInvite, Invitee } from './api';
import { VoiceCommsPanel } from './panels/VoiceCommsPanel';
import type { MatchPeer } from './session';

/** How often the browser re-asks while it is the visible panel. */
const BROWSE_INTERVAL_MS = 6000;

/**
 * Whether this node answers LAN discovery.
 *
 * Owned by the network module, not by this game — the fabric is what finds peers,
 * and a second discovery mechanism for one pane would be a second thing to debug.
 * Surfaced here because "play on LAN" is a thing people look for in a game's menu
 * and not in a network settings page.
 */
const LAN_KEY = 'network.enableLanDiscovery';

export const SENSITIVITY_KEY = 'hassault.sensitivity';
export const CONTROLS_KEY = 'hassault.controls';
export const VOLUME_KEY = 'hassault.volume';
export const FOV_KEY = 'hassault.fov';
export const CROUCH_TOGGLE_KEY = 'hassault.crouchToggle';

// ---- settings ---------------------------------------------------------------

const SENS_MIN = 0.1;
const SENS_MAX = 4;
export const FOV_MIN = 60;
export const FOV_MAX = 110;

function Slider({
  label,
  hint,
  value,
  min,
  max,
  step,
  suffix,
  fallback,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix: string;
  fallback: number;
  onChange: (v: number) => void;
}) {
  return (
    <div style={styles.row}>
      <div style={styles.rowMain}>
        <span>{label}</span>
        <span style={styles.dim}>{hint}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          style={{ width: 170 }}
          aria-label={label}
        />
        <code style={{ width: 48, textAlign: 'right' }}>
          {step < 1 ? value.toFixed(2) : Math.round(value)}
          {suffix}
        </code>
        <button onClick={() => onChange(fallback)} disabled={value === fallback}>
          Reset
        </button>
      </div>
    </div>
  );
}

/**
 * Sensitivity, field of view, volume, and how crouch behaves.
 *
 * Every one is written straight through to the app's settings store, so it survives
 * a reload and shows the same value on the settings page. The control *map* is
 * deliberately not there — it is a JSON document rather than a scalar, and the
 * settings page renders scalars — which is why `ControlsPanel` is its only editor.
 */
export function SettingsPanel() {
  const sensitivity = useSetting<number>(SENSITIVITY_KEY) ?? 1;
  const fov = useSetting<number>(FOV_KEY) ?? 75;
  const volume = useSetting<number>(VOLUME_KEY) ?? 0.7;
  const crouchToggle = useSetting<boolean>(CROUCH_TOGGLE_KEY) ?? false;

  return (
    <div style={styles.rows}>
      <Slider
        label="Mouse sensitivity"
        hint="Multiplies the turn per pixel of mouse movement. This game only."
        value={sensitivity}
        min={SENS_MIN}
        max={SENS_MAX}
        step={0.05}
        suffix="×"
        fallback={1}
        onChange={(v) => void setSetting(SENSITIVITY_KEY, v)}
      />
      <Slider
        label="Field of view"
        hint="Wider sees more and makes movement feel faster. Applies immediately."
        value={fov}
        min={FOV_MIN}
        max={FOV_MAX}
        step={1}
        suffix="°"
        fallback={75}
        onChange={(v) => void setSetting(FOV_KEY, v)}
      />
      <Slider
        label="Volume"
        hint="Footsteps, shots and landings — all synthesized, so there is nothing to download."
        value={volume}
        min={0}
        max={1}
        step={0.05}
        suffix=""
        fallback={0.7}
        onChange={(v) => void setSetting(VOLUME_KEY, v)}
      />
      <div style={styles.row}>
        <div style={styles.rowMain}>
          <span>Crouch</span>
          <span style={styles.dim}>
            Hold is what the movement rewards — a crouch you can release the instant you need speed.
            Toggle is easier on the hand.
          </span>
        </div>
        <div style={{ display: 'flex', gap: '0.25rem' }}>
          {[
            { on: false, label: 'Hold' },
            { on: true, label: 'Toggle' },
          ].map((option) => (
            <button
              key={option.label}
              onClick={() => void setSetting(CROUCH_TOGGLE_KEY, option.on)}
              style={{
                ...styles.choice,
                ...(crouchToggle === option.on ? styles.choiceActive : null),
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ marginTop: '1rem' }}>
        <VoiceCommsPanel />
      </div>
    </div>
  );
}

// ---- the server browser -----------------------------------------------------

export interface ServerBrowserProps {
  /** Maps this node can actually load, so an unjoinable row says why. */
  maps: MapSummary[];
  /** Who is in the match we are already in, if any. */
  peers: MatchPeer[];
  playerId: string;
  room: string;
  hosting: boolean;
  onJoin: (room: string, map: string, host: string) => void;
  onInvite: (friendCode: string) => void;
}

export function ServerBrowserPanel(props: ServerBrowserProps) {
  const [filter, setFilter] = useState('');
  const [matches, setMatches] = useState<BrowseMatch[]>([]);
  const [players, setPlayers] = useState<BrowsePlayer[]>([]);
  const [partial, setPartial] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const lan = useSetting<boolean>(LAN_KEY) ?? false;

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
    // Polled while this panel is open and nowhere else: the fan-out waits on other
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
        />
        <button onClick={() => void refresh()}>Refresh</button>
      </div>

      {/* LAN is a *fabric* transport, not a game feature — this row exists because
          "play on LAN" is something people look for in a game menu rather than in
          a network settings page. With it on, machines on the same network find
          each other with no friend request and their matches appear below. */}
      <div style={styles.row}>
        <div style={styles.rowMain}>
          <span>Local network</span>
          <span style={styles.dim}>
            {lan
              ? 'This machine answers LAN discovery — matches on your network appear below without a friend request.'
              : 'Off. Turn it on to find people on the same network without adding them as friends first.'}
          </span>
        </div>
        <button onClick={() => void setSetting(LAN_KEY, !lan)}>
          {lan ? 'Turn off' : 'Turn on'}
        </button>
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
          Nothing running. Host one from the menu and friends on the fabric will see it in their own
          browser.
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
                : 'Host a match first — an invite is to a room you are running.'
            }
          >
            Invite
          </button>
        </div>
      ))}
    </div>
  );
}

// ---- friends ----------------------------------------------------------------

export interface FriendsProps {
  invitees: Invitee[];
  invites: MatchInvite[];
  hosting: boolean;
  onInvite: (friendCode: string) => void;
  onAccept: (invite: MatchInvite) => void;
  onDismiss: (room: string) => void;
}

/**
 * Who you can play with, and who has asked you to.
 *
 * Separate from the server browser on purpose: that answers "what is running",
 * this answers "who is about". They are different questions and people arrive at
 * the menu with one or the other.
 */
export function FriendsPanel(props: FriendsProps) {
  return (
    <div>
      <h4 style={styles.heading}>Invitations ({props.invites.length})</h4>
      {props.invites.length === 0 && (
        <div style={styles.dim}>Nothing waiting. An invite from a friend appears here.</div>
      )}
      {props.invites.map((invite) => (
        <div key={invite.room} style={styles.row}>
          <div style={styles.rowMain}>
            <span>
              <strong>{invite.hostName}</strong> invited you to <code>{invite.map}</code>
            </span>
            <span style={styles.dim}>on {invite.host.slice(0, 8)}</span>
          </div>
          <button onClick={() => props.onAccept(invite)}>Join</button>
          <button onClick={() => props.onDismiss(invite.room)}>Dismiss</button>
        </div>
      ))}

      <h4 style={styles.heading}>Friends ({props.invitees.length})</h4>
      {props.invitees.length === 0 && (
        <div style={styles.dim}>
          Nobody online. The roster lives in the Social panel — accepting a friend there is what
          makes them reachable here, on every machine they own.
        </div>
      )}
      {props.invitees.map((f) => (
        <div key={f.person_id} style={styles.row}>
          <div style={styles.rowMain}>
            <span>{f.name}</span>
            <span style={styles.dim}>
              {f.devices_online} device{f.devices_online === 1 ? '' : 's'} online
              {f.can_play ? '' : ' · no match support on their build'}
            </span>
          </div>
          <button
            onClick={() => props.onInvite(f.friend_code)}
            disabled={!props.hosting || !f.can_play}
            title={
              props.hosting
                ? // An invite goes to every machine they have online: you invite a
                  // person and they choose which box to answer on.
                  'Invites every machine they have online'
                : 'Host a match first — an invite is to a room you are running.'
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

/**
 * The rebind table.
 *
 * Captures through a **focused input** rather than a window listener (the same
 * trick `ShortcutsPanel` uses): while a menu is open the keyboard belongs to the
 * shell again, and a window listener here would fight the dispatcher for it.
 */
export function ControlsPanel({
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
    // A bare modifier is *usually* somebody reaching for a real key — but crouch
    // genuinely wants to live on Ctrl, so a modifier held for a moment is taken as
    // itself rather than discarded forever.
    if (/^(Shift|Alt|Meta)(Left|Right)$/.test(code)) return;
    const previous = boundTo(bindings, code);
    onChange(setBinding(bindings, recording.action, recording.slot, code));
    setNote(
      previous && previous !== recording.action
        ? `${keyLabel(code)} was ${actionLabel(previous)} — that one is now unbound.`
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
          Press a key for <strong>{actionLabel(recording.action)}</strong> — Esc to cancel.
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

function actionLabel(action: GameAction): string {
  return ACTIONS.find((a) => a.action === action)?.label ?? action;
}

// ---- shared styles ----------------------------------------------------------

export const styles: Record<string, React.CSSProperties> = {
  rows: { display: 'flex', flexDirection: 'column' },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.8rem',
    padding: '0.45rem 0',
    borderBottom: '1px solid rgba(255,255,255,0.055)',
  },
  rowMain: { display: 'flex', flexDirection: 'column', gap: 2, flex: 1, minWidth: 0 },
  dim: { color: 'var(--text-dim)', fontSize: '0.74rem', lineHeight: 1.45 },
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
  keycapActive: {
    border: '1px solid var(--accent, #6ea8fe)',
    color: 'var(--accent, #6ea8fe)',
  },
  keycapEmpty: { opacity: 0.45 },
  clear: { padding: '0.1rem 0.35rem' },
  choice: {
    background: 'transparent',
    border: '1px solid var(--border, #2a2a2a)',
    color: 'var(--text-dim)',
    padding: '0.25rem 0.7rem',
    borderRadius: 5,
    cursor: 'pointer',
  },
  choiceActive: {
    background: 'rgba(110,168,254,0.14)',
    // The shorthand, not `borderColor`: spread over `choice`, which sets `border`,
    // and mixing a shorthand with its own longhand is what React warns about.
    border: '1px solid var(--accent, #6ea8fe)',
    color: 'var(--text)',
  },
};
