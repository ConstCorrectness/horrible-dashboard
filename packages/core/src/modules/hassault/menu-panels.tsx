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
import { addFriend, searchDirectory, type DirectoryEntry } from '../social/api';
import { browseServers, type BrowseMatch, type MapSummary } from './api';
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

/**
 * Whether Play, Train and Host open the native window instead of playing here.
 *
 * A setting rather than a per-press choice: which client you play in is a
 * standing preference, and putting it on every button would put a decision in
 * front of somebody who wanted to press Play. Off by default — the native client
 * has no HUD, no weapon model and no sound, so the pane is still the complete
 * game and the honest default.
 */
export const NATIVE_CLIENT_KEY = 'hassault.nativeClient';

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
  const nativeClient = useSetting<boolean>(NATIVE_CLIENT_KEY) ?? false;

  return (
    <div style={styles.rows}>
      <Slider
        label="Mouse sensitivity"
        hint="Turn per pixel of mouse movement, this game only."
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
        hint="Applies immediately, mid-match included."
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
        hint="Footsteps, shots and landings."
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
          <span style={styles.dim}>Hold releases the instant you need speed back.</span>
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

      <div style={styles.row}>
        <div style={styles.rowMain}>
          <span>Client</span>
          <span style={styles.dim}>
            The native client renders on the GPU with raw mouse input and no frame cap, in its own
            window. It has no HUD, no weapon model and no sound yet, so this pane is still the
            complete game — and it has to be built first (`cargo build --release`).
          </span>
        </div>
        <div style={{ display: 'flex', gap: '0.25rem' }}>
          {[
            { on: false, label: 'This pane' },
            { on: true, label: 'Native' },
          ].map((option) => (
            <button
              key={option.label}
              onClick={() => void setSetting(NATIVE_CLIENT_KEY, option.on)}
              style={{
                ...styles.choice,
                ...(nativeClient === option.on ? styles.choiceActive : null),
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
  const [partial, setPartial] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const lan = useSetting<boolean>(LAN_KEY) ?? false;

  const refresh = useCallback(async () => {
    try {
      const data = await browseServers();
      setMatches(data.matches);
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
          <span style={styles.dim}>{lan ? 'On' : 'Off'}</span>
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

      {/* The roster used to be rendered a second time here, as a "Players"
          list with its own Invite buttons — the same people, the same actions,
          two implementations that had already drifted (this copy disabled Invite
          for anyone already in a match; the Friends copy did not). Servers
          answers "what is running"; Friends answers "who is about". They are
          different questions, which is what this panel's own doc comment says,
          and one of them belongs in one place. */}
      <h4 style={styles.heading}>In your match ({props.peers.length})</h4>
      {props.peers.length === 0 && <div style={styles.dim}>Nobody yet.</div>}
      {props.peers.map((p) => (
        <div key={p.id} style={styles.row}>
          <div style={styles.rowMain}>
            <span>
              {p.name}
              {p.id === props.playerId ? ' (you)' : ''}
            </span>
            <span style={styles.dim}>
              {p.bot ? 'bot' : `${Math.round(p.rtt)} ms`} · {p.kills}/{p.deaths}
              {p.alive ? '' : ' · down'}
            </span>
          </div>
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
  /**
   * Invite somebody. **Not** conditional on already hosting any more — see
   * `FriendsPanel`. The caller starts a match first when there isn't one.
   */
  onInvite: (friendCode: string) => void;
  onAccept: (invite: MatchInvite) => void;
  onDismiss: (room: string) => void;
}

/** Debounce on the directory search, same as `people/DiscoverSection`. */
const SEARCH_DEBOUNCE_MS = 250;

/**
 * Find somebody by `@username` and send them a friend request, without leaving
 * the game.
 *
 * The panel used to say "the roster lives in the Social panel" and stop there,
 * which made an empty friends list a dead end: the one screen where you have just
 * discovered you have nobody to play with was the one screen that could not do
 * anything about it.
 *
 * `searchDirectory` and `addFriend` are the social module's own client — the same
 * pair `people/DiscoverSection` uses — so this is a second *view*, not a second
 * implementation, and there is no new backend route behind it. The request is sent
 * as `@username` rather than the person id already in hand, deliberately: the
 * backend re-resolves and re-checks the key fingerprint itself, because the
 * browser is not a trusted source of person ids.
 */
function AddFriend() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DirectoryEntry[]>([]);
  const [minPrefix, setMinPrefix] = useState(3);
  const [note, setNote] = useState('');
  // Which query the in-flight response belongs to: a slow request for "ro" can
  // otherwise land after a fast one for "robert" and repopulate the list with
  // stale, broader results.
  const latest = useRef('');

  useEffect(() => {
    const q = query.trim().replace(/^@/, '');
    latest.current = q;
    if (q.length < minPrefix) {
      setResults([]);
      return;
    }
    const timer = window.setTimeout(() => {
      void searchDirectory(q)
        .then((res) => {
          if (latest.current !== q) return;
          setResults(res.results ?? []);
          if (typeof res.min_prefix === 'number') setMinPrefix(res.min_prefix);
        })
        .catch(() => {
          if (latest.current === q) setResults([]);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query, minPrefix]);

  const add = async (entry: DirectoryEntry) => {
    const res = await addFriend(`@${entry.handle}`);
    setNote(res.error ? res.error : `Friend request sent to @${entry.handle}.`);
    setQuery('');
  };

  return (
    <div>
      <div style={styles.toolbar}>
        <input
          type="search"
          placeholder="Add someone by @username…"
          aria-label="Find someone by username"
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1 }}
        />
      </div>
      {note && <div style={styles.dim}>{note}</div>}
      {results.map((entry) => (
        <div key={entry.person_id} style={styles.row}>
          <div style={styles.rowMain}>
            <span>@{entry.handle}</span>
            {entry.display_name && entry.display_name !== entry.handle && (
              <span style={styles.dim}>{entry.display_name}</span>
            )}
          </div>
          <button onClick={() => void add(entry)}>Add</button>
        </div>
      ))}
    </div>
  );
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
      {props.invites.length === 0 && <div style={styles.dim}>Nothing waiting.</div>}
      {props.invites.map((invite) => (
        <div key={invite.room} style={styles.row}>
          <div style={styles.rowMain}>
            <span>
              {/* A person, not a machine. `hostName` is now their `@username`,
                  resolved roster-first by the backend; the device they sent it
                  from is the secondary line, which is worth keeping because an
                  invite fans out to every machine they have online. */}
              <strong>{invite.hostName}</strong> invited you to <code>{invite.map}</code>
            </span>
            <span style={styles.dim}>
              {invite.hostDevice || invite.host.slice(0, 8)}
            </span>
          </div>
          <button onClick={() => props.onAccept(invite)}>Join</button>
          <button onClick={() => props.onDismiss(invite.room)}>Dismiss</button>
        </div>
      ))}

      <h4 style={styles.heading}>Friends ({props.invitees.length})</h4>
      <AddFriend />
      {props.invitees.length === 0 && <div style={styles.dim}>Nobody online.</div>}
      {props.invitees.map((f) => (
        <div key={f.person_id} style={styles.row}>
          <div style={styles.rowMain}>
            <span>{f.username ? `@${f.username}` : f.name}</span>
            <span style={styles.dim}>
              {/* Presence beyond a dot: what they are actually doing. The backend
                  already knew this (`/invitees` reads `fabric.hosted_rooms`) and
                  was throwing it away. */}
              {f.room
                ? `playing ${f.room_map || 'a match'}`
                : `${f.devices_online} device${f.devices_online === 1 ? '' : 's'} online`}
              {f.can_play ? '' : ' · no match support on their build'}
            </span>
          </div>
          <button
            onClick={() => props.onInvite(f.friend_code)}
            disabled={!f.can_play}
            // No longer gated on already hosting. "Invite" is a complete intent,
            // and making somebody guess that it needs a match to exist first —
            // then go and start one, then come back — is why this panel read as
            // broken. The caller hosts on the selected map when there is nothing
            // to invite them to.
            title={
              props.hosting
                ? 'Invites every machine they have online'
                : 'Starts a match on the selected map, then invites them'
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
  /** A standing condition the section is under — an accent edge, not a glowing box. */
  notice: {
    borderLeft: '2px solid var(--accent, #6ea8fe)',
    background: 'rgba(110,168,254,0.08)',
    padding: '0.4rem 0.6rem',
    fontSize: '0.74rem',
    lineHeight: 1.45,
    color: 'var(--text-dim)',
  },
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
