/**
 * The Server window: run rooms on this node without being in them.
 *
 * Hosting used to exist only as a row of the in-game main menu, which binds it to
 * *playing* — the pane that hosts is the pane that joins, so fielding bots or
 * watching who is connected meant being in the match yourself. This is the
 * dedicated-server view of the same rooms: open one, fill it with bots, invite
 * friends, see the roster, and only then — optionally — step into it from the
 * game window.
 *
 * Every verb here is a REST call the agent's `hassault.*` tools already make
 * (`/matches`, `/matches/{room}/bots`, `/matches/{room}/invite`), so the window
 * and the agent can never disagree about a room. Nothing here holds a socket:
 * joining is the game window's job (`requestJoin` parks the intent there).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { registry } from '../../../registry';
import {
  addBots,
  createMatch,
  getMatchRoster,
  inviteToMatch,
  listInvitees,
  listMaps,
  listMatches,
  listModes,
  removeBots,
  type BotSkill,
  type Invitee,
  type MapSummary,
  type MatchRoster,
  type MatchSummary,
  type ModeSpec,
} from '../api';
import { requestJoin } from '../invite-notify';

/** How often the room list and roster refresh. REST, so a poll — cheap by design. */
const POLL_MS = 2500;
const BOT_COUNTS = [0, 1, 3, 5, 7];
const SKILLS: BotSkill[] = ['easy', 'normal', 'hard'];

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function ServerPanel() {
  const [maps, setMaps] = useState<MapSummary[]>([]);
  const [modes, setModes] = useState<ModeSpec[]>([]);
  const [rooms, setRooms] = useState<MatchSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    listMaps()
      .then(setMaps)
      .catch((e) => setError(errorText(e)));
    listModes()
      .then(setModes)
      .catch(() => setModes([]));
  }, []);

  const refreshRooms = useCallback(() => {
    listMatches()
      .then((list) => {
        setRooms(list);
        // Keep the selection while its room lives; otherwise fall to the first
        // room, so a single running match is shown without a click.
        setSelected((cur) => (cur && list.some((r) => r.id === cur) ? cur : (list[0]?.id ?? null)));
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => {
    refreshRooms();
    const t = setInterval(refreshRooms, POLL_MS);
    return () => clearInterval(t);
  }, [refreshRooms]);

  return (
    <div style={s.root}>
      <div style={s.grid}>
        <HostForm
          maps={maps}
          modes={modes}
          onHosted={(id) => {
            setSelected(id);
            refreshRooms();
          }}
          onError={setError}
        />
        <section style={s.card}>
          <h2 style={s.heading}>Rooms on this node</h2>
          {rooms.length === 0 ? (
            <p style={s.dim}>
              No rooms are running. Host one on the left — it stays open for a grace period even
              with nobody in it, long enough to invite someone.
            </p>
          ) : (
            <div role="listbox" aria-label="Rooms" style={s.roomList}>
              {rooms.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  role="option"
                  aria-selected={r.id === selected}
                  className="hassault-server-room"
                  style={{ ...s.roomRow, ...(r.id === selected ? s.roomRowActive : null) }}
                  onClick={() => setSelected(r.id)}
                >
                  <span style={s.roomMap}>{r.map}</span>
                  <span style={s.mono}>
                    {r.players - r.bots}
                    {r.bots ? ` + ${r.bots} bots` : ''} / {r.maxPlayers}
                  </span>
                  <span style={s.monoDim}>{r.id}</span>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>
      {error && (
        <p role="alert" style={s.error}>
          {error}
        </p>
      )}
      {selected && <RoomDetail room={selected} onError={setError} />}
    </div>
  );
}

function HostForm({
  maps,
  modes,
  onHosted,
  onError,
}: {
  maps: MapSummary[];
  modes: ModeSpec[];
  onHosted: (room: string) => void;
  onError: (msg: string) => void;
}) {
  const [map, setMap] = useState('');
  const [mode, setMode] = useState('dm');
  const [bots, setBots] = useState(3);
  const [skill, setSkill] = useState<BotSkill>('normal');
  const [busy, setBusy] = useState(false);
  // Bundled maps first: they need no install, so they are the ones guaranteed
  // to exist on a friend's node too.
  const ordered = useMemo(
    () =>
      [...maps].sort(
        (a, b) =>
          Number(b.source === 'bundled') - Number(a.source === 'bundled') ||
          a.name.localeCompare(b.name),
      ),
    [maps],
  );
  const chosen = map || ordered[0]?.name || '';

  const host = async () => {
    if (!chosen) return;
    setBusy(true);
    onError('');
    try {
      const room = await createMatch(chosen, mode);
      if (bots > 0) await addBots(room.id, bots, skill);
      onHosted(room.id);
    } catch (e) {
      onError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section style={s.card}>
      <h2 style={s.heading}>Host a room</h2>
      <label style={s.field}>
        <span style={s.label}>Map</span>
        <select value={chosen} onChange={(e) => setMap(e.target.value)} style={s.select}>
          {ordered.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
              {m.source === 'bundled' ? '' : ` · ${m.source}`}
            </option>
          ))}
        </select>
      </label>
      {modes.length > 0 && (
        <label style={s.field}>
          <span style={s.label}>Mode</span>
          <select value={mode} onChange={(e) => setMode(e.target.value)} style={s.select}>
            {modes.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <div style={s.field}>
        <span style={s.label}>Bots</span>
        <div role="radiogroup" aria-label="Bot count" style={s.segment}>
          {BOT_COUNTS.map((n) => (
            <button
              key={n}
              type="button"
              role="radio"
              aria-checked={bots === n}
              className="hassault-server-seg"
              style={{ ...s.segBtn, ...(bots === n ? s.segBtnOn : null) }}
              onClick={() => setBots(n)}
            >
              {n}
            </button>
          ))}
        </div>
      </div>
      <label style={s.field}>
        <span style={s.label}>Skill</span>
        <select
          value={skill}
          onChange={(e) => setSkill(e.target.value as BotSkill)}
          style={s.select}
          disabled={bots === 0}
        >
          {SKILLS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </label>
      <button type="button" disabled={busy || !chosen} onClick={() => void host()}>
        {busy ? 'Opening…' : 'Host room'}
      </button>
    </section>
  );
}

function RoomDetail({ room, onError }: { room: string; onError: (msg: string) => void }) {
  const [roster, setRoster] = useState<MatchRoster | null>(null);
  const [invitees, setInvitees] = useState<Invitee[]>([]);
  const [who, setWho] = useState('');
  const [note, setNote] = useState('');

  const refresh = useCallback(() => {
    getMatchRoster(room)
      .then(setRoster)
      .catch(() => setRoster(null));
  }, [room]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    listInvitees()
      .then(setInvitees)
      .catch(() => setInvitees([]));
  }, []);

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    onError('');
    try {
      await fn();
      if (done) setNote(done);
      refresh();
    } catch (e) {
      onError(errorText(e));
    }
  };

  const join = () => {
    registry.openPanel('hassault.play');
    requestJoin({ room, map: roster?.map ?? '', host: '', kind: 'match' });
  };

  if (!roster) return null;
  const botCount = roster.players.filter((p) => p.bot).length;
  const playable = invitees.filter((i) => i.can_play);

  return (
    <section style={{ ...s.card, ...s.detail }}>
      <header style={s.detailHead}>
        <div>
          <h2 style={s.heading}>{roster.map}</h2>
          <span style={s.monoDim}>
            {roster.room} · {roster.mode} · {roster.players.length}/{roster.capacity}
          </span>
        </div>
        <div style={s.actions}>
          <button type="button" onClick={() => void act(() => addBots(room, 1, 'normal'))}>
            Add bot
          </button>
          <button
            type="button"
            disabled={botCount === 0}
            onClick={() => void act(() => removeBots(room, 1))}
          >
            Remove bot
          </button>
          <button
            type="button"
            disabled={botCount === 0}
            onClick={() => void act(() => removeBots(room))}
          >
            Clear bots
          </button>
          <button type="button" onClick={join}>
            Join in game
          </button>
        </div>
      </header>

      <table style={s.table}>
        <thead>
          <tr>
            {['Player', 'Team', 'K', 'D', 'Ping', ''].map((h) => (
              <th key={h} style={s.th}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {roster.players.length === 0 && (
            <tr>
              <td colSpan={6} style={{ ...s.td, ...s.dim }}>
                Empty — nobody has joined yet.
              </td>
            </tr>
          )}
          {roster.players.map((p) => (
            <tr key={`${p.name}-${p.team}`}>
              <td style={s.td}>{p.name}</td>
              <td style={{ ...s.td, ...s.mono }}>{p.team}</td>
              <td style={{ ...s.td, ...s.mono }}>{p.kills}</td>
              <td style={{ ...s.td, ...s.mono }}>{p.deaths}</td>
              <td style={{ ...s.td, ...s.mono }}>{p.bot ? '—' : `${p.rtt_ms}ms`}</td>
              <td style={{ ...s.td, ...s.monoDim }}>
                {p.bot ? 'bot' : p.remote ? 'via fabric' : 'local'}
                {p.alive ? '' : ' · down'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form
        style={s.invite}
        onSubmit={(e) => {
          e.preventDefault();
          if (who.trim())
            void act(() => inviteToMatch(room, who.trim()), `Invite sent to ${who.trim()}.`);
        }}
      >
        <span style={s.label}>Invite</span>
        <input
          type="text"
          list="hassault-server-invitees"
          placeholder="friend, @username or friend code"
          value={who}
          onChange={(e) => setWho(e.target.value)}
          style={s.input}
        />
        <datalist id="hassault-server-invitees">
          {playable.map((i) => (
            <option key={i.person_id} value={i.username ? `@${i.username}` : i.friend_code}>
              {i.name}
              {i.devices_online ? ' · online' : ''}
            </option>
          ))}
        </datalist>
        <button type="submit" disabled={!who.trim()}>
          Send
        </button>
      </form>
      {note && <p style={s.dim}>{note}</p>}
    </section>
  );
}

const s: Record<string, React.CSSProperties> = {
  root: { height: '100%', overflow: 'auto', padding: '0.9rem', display: 'grid', gap: '0.8rem' },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'minmax(240px, 320px) minmax(0, 1fr)',
    gap: '0.8rem',
    alignItems: 'start',
  },
  card: {
    display: 'grid',
    gap: '0.55rem',
    padding: '0.8rem',
    border: '1px solid var(--border)',
    borderTop: '2px solid var(--accent)',
    background: 'var(--bg-raised)',
  },
  heading: {
    margin: 0,
    fontSize: '11px',
    fontWeight: 700,
    letterSpacing: '0.14em',
    textTransform: 'uppercase',
    color: 'var(--text)',
  },
  dim: { margin: 0, color: 'var(--text-dim)', fontSize: '12.5px', lineHeight: 1.45 },
  error: { margin: 0, color: 'var(--danger)', fontSize: '12.5px' },
  mono: { fontFamily: 'var(--font-mono)', fontSize: '10.5px' },
  monoDim: { fontFamily: 'var(--font-mono)', fontSize: '10.5px', color: 'var(--text-dim)' },
  field: { display: 'grid', gridTemplateColumns: '64px 1fr', alignItems: 'center', gap: '0.5rem' },
  label: {
    fontSize: '10.5px',
    fontWeight: 700,
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
    color: 'var(--text-dim)',
  },
  select: { padding: '0 0.5rem', minWidth: 0 },
  input: { padding: '0 0.6rem', minWidth: 0, flex: 1 },
  segment: { display: 'flex', border: '1px solid var(--border)' },
  segBtn: {
    flex: 1,
    height: 28,
    border: 0,
    borderRight: '1px solid var(--border)',
    background: 'transparent',
    color: 'var(--text-dim)',
    fontFamily: 'var(--font-mono)',
    cursor: 'pointer',
  },
  segBtnOn: {
    background: 'var(--bg-hover)',
    color: 'var(--text)',
    boxShadow: 'inset 0 -2px 0 var(--accent)',
  },
  roomList: { display: 'grid', gap: 2 },
  roomRow: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) auto auto',
    gap: '0.8rem',
    alignItems: 'center',
    padding: '0.45rem 0.6rem',
    border: 0,
    borderLeft: '2px solid transparent',
    background: 'transparent',
    color: 'var(--text)',
    textAlign: 'left',
    cursor: 'pointer',
  },
  roomRowActive: { background: 'var(--bg-hover)', borderLeftColor: 'var(--accent)' },
  roomMap: { fontWeight: 600, fontSize: '12.5px' },
  detail: { alignContent: 'start' },
  detailHead: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: '0.8rem',
    flexWrap: 'wrap',
  },
  actions: { display: 'flex', gap: '0.35rem', flexWrap: 'wrap' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' },
  th: {
    textAlign: 'left',
    padding: '0.3rem 0.4rem',
    borderBottom: '1px solid var(--border)',
    fontSize: '9.5px',
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
    color: 'var(--text-dim)',
    fontWeight: 600,
  },
  td: { padding: '0.3rem 0.4rem', borderBottom: '1px solid var(--border)' },
  invite: { display: 'flex', alignItems: 'center', gap: '0.5rem' },
};
