import { useEffect, useMemo, useRef, useState } from 'react';
import { useGameRooms, type GameRoom } from '../hooks/useGameRooms';
import { useRollingNumber } from '../hooks/useRollingNumber';
import { accountName, signOut, useAuth } from '../auth';
import { MAPS, mapArt, mapEntry, modeLabel } from '../maps';
import { MapBackdrop } from './MapBackdrop';

/** Where Quick Play opens a room when nothing is running. */
const QUICK_MAP = 'hd_dust2';

type Section = 'play' | 'servers' | 'maps';

export interface LandingProps {
  callsign: string;
  onCallsignChange: (name: string) => void;
  onJoin: (room: string, map: string) => void;
  onHost: (map: string) => void;
  onCreate: () => void;
  onSignIn: () => void;
  onChooseHandle: () => void;
}

/**
 * The front page: top bar, hero, live servers, maps.
 *
 * The structure follows the classic browser-shooter front door (a hero with one
 * action, then the server list), and everything on it is live — the counts are
 * the rooms this server is actually ticking, and the art behind the hero is the
 * map Quick Play would put you on.
 */
export function Landing(props: LandingProps) {
  const { rooms, loading, pingMs, refresh } = useGameRooms();
  const auth = useAuth();
  const [section, setSection] = useState<Section>('play');
  const pageRef = useRef<HTMLDivElement>(null);
  const refs = {
    play: useRef<HTMLElement>(null),
    servers: useRef<HTMLElement>(null),
    maps: useRef<HTMLElement>(null),
  };

  const players = rooms.reduce((n, r) => n + r.playerCount, 0);
  const open = rooms.filter((r) => r.playerCount < r.maxPlayers);
  // The fullest room with space — joining a populated match beats opening one.
  const best = [...open].sort((a, b) => b.playerCount - a.playerCount)[0] ?? null;
  const featured = best?.map ?? QUICK_MAP;

  const quickPlay = () => (best ? props.onJoin(best.id, best.map) : props.onHost(QUICK_MAP));

  // Nav highlights the section in view, so the bar is a position, not just links.
  useEffect(() => {
    const page = pageRef.current;
    if (!page) return;
    const onScroll = () => {
      // A section is "current" once its top passes the upper third of the view.
      const y = page.scrollTop + page.clientHeight * 0.35;
      let current: Section = 'play';
      for (const id of ['servers', 'maps'] as const) {
        const el = refs[id].current;
        if (el && el.offsetTop <= y) current = id;
      }
      setSection(current);
    };
    page.addEventListener('scroll', onScroll, { passive: true });
    return () => page.removeEventListener('scroll', onScroll);
    // The refs are stable objects; the listener reads `.current` at call time.
  }, []);

  const go = (id: Section) => {
    const el = refs[id].current;
    if (!el || !pageRef.current) return;
    pageRef.current.scrollTo({ top: id === 'play' ? 0 : el.offsetTop - 8, behavior: 'smooth' });
  };

  return (
    <div className="page" ref={pageRef}>
      <header className="topbar">
        <div className="wrap topbar-inner">
          <div className="wordmark">
            <i className="wordmark-mark" aria-hidden="true" />
            <span>
              Horrible<b>Assault</b>
            </span>
          </div>
          <nav className="nav" aria-label="Sections">
            {(['play', 'servers', 'maps'] as const).map((id) => (
              <button
                key={id}
                type="button"
                className="nav-link"
                aria-current={section === id}
                onClick={() => go(id)}
              >
                {id}
              </button>
            ))}
          </nav>
          <div className="topbar-right">
            <div className="online" title="Players in a match on this server right now">
              <i className="live-dot" aria-hidden="true" />
              <RollingCount value={players} />
              <span>online</span>
            </div>
            {pingMs !== null && (
              <span
                className={`ping ${pingMs < 80 ? 'ping-good' : pingMs < 150 ? 'ping-warn' : 'ping-bad'}`}
                title="Round trip to the game server"
              >
                {pingMs} ms
              </span>
            )}
            <AccountChip onSignIn={props.onSignIn} onChooseHandle={props.onChooseHandle} />
          </div>
        </div>
      </header>

      <section className="hero" ref={refs.play}>
        <MapBackdrop mapId={featured} />
        <div className="wrap" style={{ position: 'relative' }}>
          <div className="hero-grid">
            <div className="rise" style={{ ['--i' as string]: 0 }}>
              <div className="kicker">Browser tactical FPS</div>
              <h1 className="display hero-title">
                Drop in.
                <span>No install.</span>
              </h1>
              <p className="hero-copy">
                Rounds of tactical gunplay that start in a browser tab. Pick a server, or let Quick
                Play put you in the fullest match with a free slot.
              </p>
              <div className="hero-actions">
                <button type="button" className="btn btn-primary btn-lg" onClick={quickPlay}>
                  Quick play
                </button>
                <button type="button" className="btn btn-lg" onClick={props.onCreate}>
                  Create match
                </button>
              </div>
              <div className="stats">
                <Stat value={players} label="Players online" />
                <Stat value={rooms.length} label="Live rooms" />
                <Stat value={MAPS.length} label="Maps" />
              </div>
            </div>
            <div className="rise" style={{ ['--i' as string]: 2 }}>
              <OperatorPanel
                callsign={props.callsign}
                onCallsignChange={props.onCallsignChange}
                onSignIn={props.onSignIn}
                onChooseHandle={props.onChooseHandle}
                checking={auth.checking}
              />
            </div>
          </div>
          <div className="now-showing">
            {best ? 'Quick play joins' : 'Quick play opens'} · <b>{mapEntry(featured).title}</b>
          </div>
        </div>
      </section>

      <div className="wrap">
        <section className="section" ref={refs.servers}>
          <ServerList
            rooms={rooms}
            loading={loading}
            onJoin={props.onJoin}
            onRefresh={() => void refresh()}
            onQuickPlay={quickPlay}
          />
        </section>

        <section className="section" ref={refs.maps}>
          <div className="section-head">
            <div>
              <h2 className="display section-title">Maps</h2>
              <p className="section-sub">
                Open a room on any of them. Share the link and it fills.
              </p>
            </div>
          </div>
          <div className="map-grid">
            {MAPS.map((m, i) => {
              const art = mapArt(m.id);
              const live = rooms
                .filter((r) => r.map === m.id)
                .reduce((n, r) => n + r.playerCount, 0);
              return (
                <button
                  key={m.id}
                  type="button"
                  className="map-card rise"
                  style={{ ['--i' as string]: i }}
                  onClick={() => props.onHost(m.id)}
                >
                  {art && <img src={art} alt="" loading="lazy" />}
                  <div className="map-card-meta">
                    <span>{m.id}</span>
                    <span>{live > 0 ? `${live} playing` : m.sites ? 'A · B sites' : ''}</span>
                  </div>
                  <div className="map-card-body">
                    <div className="display">{m.title}</div>
                    <p>{m.blurb}</p>
                  </div>
                  <span className="map-card-cta">Open room</span>
                </button>
              );
            })}
          </div>
        </section>

        <footer className="footer">
          <span>WASD move · Space jump · Mouse aim and fire · Esc menu</span>
          <span>
            {auth.account?.handle
              ? 'Signed in — your matches are recorded to your account.'
              : 'Guest matches are unrated. Sign in to keep your record.'}
          </span>
        </footer>
      </div>
    </div>
  );
}

// ---- pieces -------------------------------------------------------------------

function RollingCount({ value }: { value: number }) {
  const shown = useRollingNumber(value);
  return <span>{shown}</span>;
}

function Stat({ value, label }: { value: number; label: string }) {
  const shown = useRollingNumber(value);
  return (
    <div className="stat">
      <div className="stat-value">{String(shown).padStart(2, '0')}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function AccountChip({
  onSignIn,
  onChooseHandle,
}: {
  onSignIn: () => void;
  onChooseHandle: () => void;
}) {
  const { account, checking } = useAuth();
  if (checking) return null;
  if (!account) {
    return (
      <button type="button" className="btn btn-sm" onClick={onSignIn}>
        Sign in
      </button>
    );
  }
  const name = accountName(account);
  if (!name) {
    return (
      <button type="button" className="btn btn-sm" onClick={onChooseHandle}>
        Choose username
      </button>
    );
  }
  return (
    <div className="account-chip">
      <span>{name}</span>
      <button type="button" className="btn btn-sm btn-ghost" onClick={signOut}>
        Sign out
      </button>
    </div>
  );
}

function OperatorPanel({
  callsign,
  onCallsignChange,
  onSignIn,
  onChooseHandle,
  checking,
}: {
  callsign: string;
  onCallsignChange: (name: string) => void;
  onSignIn: () => void;
  onChooseHandle: () => void;
  checking: boolean;
}) {
  const { account } = useAuth();
  const handle = accountName(account);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(callsign);

  const save = () => {
    onCallsignChange(draft);
    setEditing(false);
  };

  if (handle) {
    return (
      <div className="panel operator">
        <div className="operator-head">
          <span className="label" style={{ color: 'var(--text-dim)' }}>
            Operator
          </span>
          <span className="tag tag-accent">Signed in</span>
        </div>
        <div className="operator-name">
          <span className="display">{handle}</span>
        </div>
        <div className="mono" style={{ fontSize: 13 }}>
          {account?.display_name && account.display_name !== handle
            ? account.display_name
            : account?.id}
        </div>
        <p className="operator-note">
          Matches you play signed in are recorded to this account. A room stays unrated while any
          guest is in it.
        </p>
        <div className="operator-actions" style={{ marginTop: 18 }}>
          <button type="button" className="btn btn-sm btn-ghost" onClick={signOut}>
            Sign out
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="panel operator">
      <div className="operator-head">
        <span className="label" style={{ color: 'var(--text-dim)' }}>
          Operator
        </span>
        <span className="tag">Guest</span>
      </div>
      {editing ? (
        <form
          className="inline-edit"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <input
            type="text"
            className="input"
            aria-label="Callsign"
            maxLength={16}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
          />
          <button type="submit" className="btn">
            Save
          </button>
        </form>
      ) : (
        <div className="operator-name">
          <span className="display">{callsign}</span>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => {
              setDraft(callsign);
              setEditing(true);
            }}
          >
            Rename
          </button>
        </div>
      )}
      <p className="operator-note">
        You're playing as a guest. Sign in to keep your name and record.
      </p>
      <ul className="operator-perks">
        <li>A username that's yours on every server</li>
        <li>Rated matches and a match history</li>
        <li>The same account as the desktop app</li>
      </ul>
      {!checking && (
        <div className="operator-actions">
          {account && !account.handle ? (
            <button type="button" className="btn" onClick={onChooseHandle}>
              Choose username
            </button>
          ) : (
            <button type="button" className="btn" onClick={onSignIn}>
              Sign in or create account
            </button>
          )}
        </div>
      )}
    </div>
  );
}

const FILTERS = ['all', 'dm', 'tdm', 'defuse'] as const;

function ServerList({
  rooms,
  loading,
  onJoin,
  onRefresh,
  onQuickPlay,
}: {
  rooms: GameRoom[];
  loading: boolean;
  onJoin: (room: string, map: string) => void;
  onRefresh: () => void;
  onQuickPlay: () => void;
}) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('all');
  const modes = useMemo(() => new Set(rooms.map((r) => r.mode)), [rooms]);
  const shown = filter === 'all' ? rooms : rooms.filter((r) => r.mode === filter);
  const sorted = [...shown].sort((a, b) => b.playerCount - a.playerCount);

  return (
    <>
      <div className="section-head">
        <div>
          <h2 className="display section-title">
            Live servers<small>{rooms.length}</small>
          </h2>
          <p className="section-sub">
            Every room running on this game server. Updates every few seconds.
          </p>
        </div>
        <div className="chips">
          {FILTERS.filter((f) => f === 'all' || modes.has(f)).map((f) => (
            <button
              key={f}
              type="button"
              className="chip"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
            >
              {f === 'all' ? 'All' : modeLabel(f)}
            </button>
          ))}
          <button type="button" className="btn btn-sm btn-ghost" onClick={onRefresh}>
            Refresh
          </button>
        </div>
      </div>

      <div className="servers">
        <div className="srow srow-head" aria-hidden="true">
          <span />
          <span>Map</span>
          <span>Mode</span>
          <span>Players</span>
          <span />
        </div>
        {loading && rooms.length === 0 ? (
          <div className="empty">
            <span className="mono">Contacting the game server…</span>
          </div>
        ) : sorted.length === 0 ? (
          <div className="empty">
            <div>
              <div className="display">No rooms running</div>
              <p>
                Quick Play opens one on {mapEntry(QUICK_MAP).title} and the next player joins you.
              </p>
            </div>
            <button type="button" className="btn" onClick={onQuickPlay}>
              Open a room
            </button>
          </div>
        ) : (
          sorted.map((room, i) => <ServerRow key={room.id} room={room} index={i} onJoin={onJoin} />)
        )}
      </div>
    </>
  );
}

function ServerRow({
  room,
  index,
  onJoin,
}: {
  room: GameRoom;
  index: number;
  onJoin: (room: string, map: string) => void;
}) {
  const entry = mapEntry(room.map);
  const art = mapArt(room.map);
  const full = room.playerCount >= room.maxPlayers;
  const ratio = room.maxPlayers > 0 ? room.playerCount / room.maxPlayers : 0;
  return (
    <div className="srow rise" style={{ ['--i' as string]: index }}>
      <div className="thumb">
        {art ? <img src={art} alt="" loading="lazy" /> : room.map.replace(/^hd_/, '')}
      </div>
      <div className="srow-map">
        <div className="display">{entry.title}</div>
        <div className="mono">
          {room.map} · room {room.id}
        </div>
      </div>
      <div>
        <span className="tag">{modeLabel(room.mode)}</span>
      </div>
      <div className="fill">
        <span className="fill-count">
          <b>{room.playerCount}</b> / {room.maxPlayers}
        </span>
        <div className={`fill-bar${ratio >= 0.75 ? ' is-hot' : ''}`}>
          <i style={{ ['--fill' as string]: ratio.toFixed(3) }} />
        </div>
      </div>
      <div className="srow-action">
        <button
          type="button"
          className="btn btn-sm"
          disabled={full}
          title={full ? 'This room is full' : undefined}
          onClick={() => onJoin(room.id, room.map)}
        >
          {full ? 'Full' : 'Join'}
        </button>
      </div>
    </div>
  );
}
