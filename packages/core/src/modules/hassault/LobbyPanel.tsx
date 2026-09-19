/**
 * The lobby: a party gathered before a match.
 *
 * Inviting a friend used to drop you straight into a live match, alone, while the
 * invite was still in flight. This is the room in front of that — members, ready
 * states, chat — and **Start** is the host's call, which sends everybody into the
 * same match together. The lobby outlives the match, so leaving it puts you back
 * here with the same people.
 *
 * Everything shown is the server's `LobbyState`, pushed whole on every change
 * (`backend/modules/hassault/lobby.py`); this component renders it and sends
 * intents, and holds no copy of its own beyond the chat draft.
 *
 * See docs/modules/hassault.mdx.
 */
import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from 'react';

import type { Invitee, MapSummary, MatchInvite } from './api';
import { setSetting } from '../../settings';
import { PUSH_TO_TALK_KEY, styles } from './menu-panels';
import type { VoiceView } from './lobby-voice';
import type { LobbyMember, LobbyState } from './session';

export interface LobbyControls {
  state: LobbyState | null;
  error: string;
  create: () => void;
  leave: () => void;
  chat: (text: string) => void;
  ready: (ready: boolean) => void;
  setMap: (map: string) => void;
  start: () => void;
  /** Join the match this lobby started, while it is still running. */
  rejoin: () => void;
  /** Voice: our side of it. Who else is in voice comes from `state.members`. */
  voice: VoiceView;
  joinVoice: () => void;
  leaveVoice: () => void;
  setMuted: (muted: boolean) => void;
  setDeafened: (deafened: boolean) => void;
}

export interface LobbyPanelProps {
  lobby: LobbyControls;
  maps: MapSummary[];
  mapName: string;
  invitees: Invitee[];
  invites: MatchInvite[];
  onInvite: (friendCode: string) => void;
  onAcceptInvite: (invite: MatchInvite) => void;
  /** A match can't start without a map and a loadout; the host's button says so. */
  canStart: boolean;
}

export function LobbyPanel(props: LobbyPanelProps) {
  const { lobby } = props;
  const state = lobby.state;
  const pending = props.invites.filter((i) => i.kind === 'lobby');

  if (!state) {
    return (
      <div>
        {lobby.error && <div style={styles.error}>{lobby.error}</div>}
        <div style={styles.notice}>
          Gather friends here before a match: they join, chat and ready up, and the host starts the
          match for everyone at once.
        </div>
        <div style={styles.row}>
          <div style={styles.rowMain}>
            <span>Open a lobby</span>
            <span style={styles.dim}>
              on <code style={mono}>{props.mapName || 'the selected map'}</code>
            </span>
          </div>
          <button onClick={lobby.create} disabled={!props.mapName}>
            Create
          </button>
        </div>
        {pending.length > 0 && (
          <>
            <h4 style={styles.heading}>Lobby invites</h4>
            {pending.map((invite) => (
              <div key={invite.room} style={styles.row}>
                <div style={styles.rowMain}>
                  <span>
                    <strong>{invite.hostName}</strong> invited you to their lobby
                  </span>
                  <span style={{ ...styles.dim, ...mono }}>
                    {invite.map}
                    {invite.hostDevice ? ` · ${invite.hostDevice}` : ''}
                  </span>
                </div>
                <button onClick={() => props.onAcceptInvite(invite)}>Join</button>
              </div>
            ))}
          </>
        )}
      </div>
    );
  }

  const me = state.members.find((m) => m.id === state.you);
  const others = state.members.filter((m) => !m.host);
  const readyCount = others.filter((m) => m.ready).length;
  const inLobby = new Set(state.members.map((m) => m.name.split('@')[0]));

  return (
    <div>
      {lobby.error && <div style={styles.error}>{lobby.error}</div>}

      <div style={bar}>
        <div style={styles.rowMain}>
          <span style={title}>Lobby</span>
          <span style={{ ...styles.dim, ...mono }}>
            {state.members.length}/{state.maxPlayers} · {state.mode || 'dm'}
            {state.host ? ' · hosted by a friend' : ''}
          </span>
        </div>
        {state.isHost ? (
          <select
            value={state.map}
            onChange={(e) => lobby.setMap(e.target.value)}
            aria-label="Lobby map"
            style={{ width: 180, padding: '0 0.4rem' }}
          >
            {props.maps.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name}
              </option>
            ))}
          </select>
        ) : (
          <code style={mono}>{state.map}</code>
        )}
      </div>

      <h4 style={styles.heading}>Members</h4>
      <div style={styles.rows}>
        {state.members.map((m, i) => (
          <div
            key={m.id}
            style={{
              ...styles.row,
              animation: `hd-lobby-in 180ms ease-out ${Math.min(i, 6) * 40}ms both`,
            }}
          >
            <VoiceGlyph member={m} voice={lobby.voice} self={m.id === state.you} />
            <div style={styles.rowMain}>
              <span style={mono}>
                {m.name}
                {m.id === state.you ? <span style={styles.dim}> (you)</span> : null}
              </span>
            </div>
            {m.host ? (
              <Chip tone="accent">host</Chip>
            ) : m.ready ? (
              <Chip tone="ok">ready</Chip>
            ) : (
              <Chip tone="dim">not ready</Chip>
            )}
          </div>
        ))}
      </div>

      <div style={{ ...styles.toolbar, marginTop: '0.6rem', flexWrap: 'wrap' }}>
        {state.isHost ? (
          <button
            onClick={lobby.start}
            disabled={!props.canStart}
            style={primary}
            title={
              others.length > 0 && readyCount < others.length
                ? `${readyCount} of ${others.length} ready — starting anyway sends everyone in`
                : undefined
            }
          >
            {state.room ? 'Start again' : 'Start match'}
          </button>
        ) : (
          <button onClick={() => lobby.ready(!me?.ready)} style={me?.ready ? undefined : primary}>
            {me?.ready ? 'Not ready' : 'Ready'}
          </button>
        )}
        {state.room && <button onClick={lobby.rejoin}>Join match in progress</button>}
        <button onClick={lobby.leave}>{state.isHost ? 'Close lobby' : 'Leave lobby'}</button>
        {state.isHost && others.length > 0 && (
          <span style={{ ...styles.dim, ...mono }}>
            {readyCount}/{others.length} ready
          </span>
        )}
      </div>

      <h4 style={styles.heading}>Voice</h4>
      <VoiceBar lobby={lobby} state={state} />

      <h4 style={styles.heading}>Chat</h4>
      <LobbyChat state={state} onSend={lobby.chat} />

      {state.isHost && (
        <>
          <h4 style={styles.heading}>Invite</h4>
          {props.invitees.length === 0 && <div style={styles.dim}>No friends online.</div>}
          {props.invitees.map((f) => {
            const handle = f.username || f.name;
            const here = inLobby.has(handle);
            return (
              <div key={f.person_id} style={styles.row}>
                <div style={styles.rowMain}>
                  <span style={mono}>{f.username ? `@${f.username}` : f.name}</span>
                  <span style={styles.dim}>
                    {f.room
                      ? `playing ${f.room_map || 'a match'}`
                      : `${f.devices_online} device${f.devices_online === 1 ? '' : 's'} online`}
                  </span>
                </div>
                <button
                  onClick={() => props.onInvite(f.friend_code)}
                  disabled={!f.can_play || here}
                >
                  {here ? 'In lobby' : 'Invite'}
                </button>
              </div>
            );
          })}
        </>
      )}
      <style>{KEYFRAMES}</style>
    </div>
  );
}

function LobbyChat({ state, onSend }: { state: LobbyState; onSend: (text: string) => void }) {
  const [draft, setDraft] = useState('');
  const logRef = useRef<HTMLDivElement | null>(null);
  const count = state.chat.length;

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [count]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return;
    onSend(draft);
    setDraft('');
  };

  return (
    <div>
      <div ref={logRef} style={log} aria-live="polite">
        {count === 0 && <div style={styles.dim}>No messages yet.</div>}
        {state.chat.map((line, i) =>
          line.from ? (
            <div key={`${line.ts}-${i}`} style={{ lineHeight: 1.5 }}>
              <span
                style={{
                  ...mono,
                  color:
                    line.memberId === state.you
                      ? 'var(--accent, #6ea8fe)'
                      : 'var(--text-secondary, #94a3b8)',
                }}
              >
                {line.from}
              </span>{' '}
              <span>{line.text}</span>
            </div>
          ) : (
            <div key={`${line.ts}-${i}`} style={{ ...styles.dim, ...mono }}>
              — {line.text}
            </div>
          ),
        )}
      </div>
      <form onSubmit={submit} style={{ display: 'flex', gap: '0.4rem', marginTop: '0.4rem' }}>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Say something…"
          aria-label="Lobby chat message"
          maxLength={200}
          autoComplete="off"
          style={{ flex: 1, padding: '0 0.6rem' }}
        />
        <button type="submit" disabled={!draft.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}

/**
 * Join/leave, mute and deafen. Open mic while joined — a lobby is a
 * conversation, and push-to-talk would need a key the menu does not own.
 */
function VoiceBar({ lobby, state }: { lobby: LobbyControls; state: LobbyState }) {
  const v = lobby.voice;
  const inVoice = state.members.filter((m) => m.voice);
  const others = inVoice.filter((m) => m.id !== state.you);
  const connected = others.filter((m) => v.links[m.id] === 'connected').length;

  if (!v.active) {
    return (
      <div>
        <div style={{ ...styles.toolbar, marginBottom: 0 }}>
          <button onClick={lobby.joinVoice} disabled={v.starting}>
            {v.starting ? 'Opening mic…' : 'Join voice'}
          </button>
          <span style={{ ...styles.dim, ...mono }}>
            {inVoice.length === 0 ? 'nobody in voice' : `${inVoice.length} in voice`}
          </span>
        </div>
        {v.error && <div style={styles.error}>{v.error}</div>}
      </div>
    );
  }

  return (
    <div style={{ ...styles.toolbar, marginBottom: 0, flexWrap: 'wrap' }}>
      <button
        onClick={() => lobby.setMuted(!v.muted)}
        aria-pressed={v.muted}
        style={v.muted ? warn : undefined}
      >
        <MicIcon off={v.muted} /> {v.muted ? 'Unmute' : 'Mute'}
      </button>
      <button
        onClick={() => lobby.setDeafened(!v.deafened)}
        aria-pressed={v.deafened}
        style={v.deafened ? warn : undefined}
      >
        {v.deafened ? 'Undeafen' : 'Deafen'}
      </button>
      <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}>
        <input
          type="checkbox"
          checked={v.pushToTalk}
          onChange={(e) => void setSetting(PUSH_TO_TALK_KEY, e.target.checked)}
        />
        <span>Push to talk</span>
      </label>
      <button onClick={lobby.leaveVoice}>Leave voice</button>
      <span style={{ ...styles.dim, ...mono }}>
        {others.length === 0
          ? 'waiting for others to join voice'
          : `${connected}/${others.length} connected`}
        {v.pushToTalk && !v.muted
          ? v.talking
            ? ' · transmitting'
            : ' · hold Push to talk to speak'
          : ''}
      </span>
    </div>
  );
}

/** Mic state beside a member: speaking, muted, in voice, or nothing. */
function VoiceGlyph({
  member,
  voice,
  self,
}: {
  member: LobbyMember;
  voice: VoiceView;
  self: boolean;
}) {
  const inVoice = self ? voice.active : member.voice;
  const muted = self ? voice.muted : member.muted;
  const speaking = voice.speaking.includes(member.id);
  const failed = !self && voice.links[member.id] === 'failed';
  const color = !inVoice
    ? 'transparent'
    : failed || muted
      ? 'var(--danger, #f87171)'
      : speaking
        ? 'var(--success, #4ade80)'
        : 'var(--text-dim, #8b93a7)';
  return (
    <span
      style={{
        display: 'inline-flex',
        width: 18,
        justifyContent: 'center',
        color,
        filter: speaking ? 'drop-shadow(0 0 3px var(--success, #4ade80))' : undefined,
        transition: 'color 120ms',
      }}
      title={
        !inVoice
          ? undefined
          : failed
            ? 'Could not connect audio'
            : muted
              ? 'Muted'
              : speaking
                ? 'Speaking'
                : 'In voice'
      }
      aria-hidden={!inVoice}
    >
      {inVoice && <MicIcon off={muted} />}
    </span>
  );
}

function MicIcon({ off }: { off: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ verticalAlign: '-1px' }}
    >
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v4" />
      {off && <path d="M3 3l18 18" />}
    </svg>
  );
}

function Chip({ tone, children }: { tone: 'ok' | 'accent' | 'dim'; children: string }) {
  const color =
    tone === 'ok'
      ? 'var(--success, #4ade80)'
      : tone === 'accent'
        ? 'var(--accent, #6ea8fe)'
        : 'var(--text-dim, #8b93a7)';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.35rem',
        padding: '0.1rem 0.5rem',
        borderRadius: 999,
        fontSize: '0.68rem',
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        color,
        background: `color-mix(in srgb, ${color} 14%, transparent)`,
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: 999, background: color }} />
      {children}
    </span>
  );
}

const KEYFRAMES = `@keyframes hd-lobby-in { from { opacity: 0; transform: translateX(-4px); } to { opacity: 1; transform: none; } }`;

const mono: CSSProperties = {
  fontFamily: 'var(--font-mono, "JetBrains Mono", Consolas, monospace)',
};

const title: CSSProperties = {
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
};

const bar: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '0.8rem',
  padding: '0.5rem 0.6rem',
  borderTop: '2px solid var(--accent, #6ea8fe)',
  background: 'rgba(110,168,254,0.06)',
};

const log: CSSProperties = {
  height: 150,
  overflowY: 'auto',
  padding: '0.4rem 0.5rem',
  border: '1px solid rgba(150,160,190,.22)',
  borderRadius: 4,
  background: 'rgba(0,0,0,0.25)',
  fontSize: '0.78rem',
};

const warn: CSSProperties = {
  color: 'var(--danger, #f87171)',
  borderColor: 'color-mix(in srgb, var(--danger, #f87171) 45%, transparent)',
};

const primary: CSSProperties = {
  background: 'var(--accent, #6ea8fe)',
  border: '1px solid transparent',
  color: '#08111f',
  fontWeight: 600,
  borderRadius: 5,
  cursor: 'pointer',
};
