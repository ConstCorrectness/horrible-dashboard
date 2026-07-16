import { useState } from 'react';

import { townJoin, townLeave, townWhisper, useGames, type TownEvent } from '../game-ws';
import { openGamesSection } from '../hub-section';
import { TownMapCanvas } from './TownMapCanvas';

const AVATARS = ['🐠', '🐙', '🦀', '🦜', '🐢', '🦊', '🐸', '🦉'];
const PHASE_ICON: Record<string, string> = {
  morning: '🌅',
  afternoon: '☀️',
  evening: '🌆',
  night: '🌙',
};
const DEFAULT_PLACES = [
  'fountain',
  'bakery',
  'tavern',
  'library',
  'docks',
  'residential_zone',
  'gym',
  'workplace',
];

function eventText(e: TownEvent): string {
  switch (e.type) {
    case 'say':
      return `${e.avatar} ${e.name}: “${e.text}”`;
    case 'emote':
      return `${e.avatar} ${e.name} ${e.text}`;
    case 'move':
      return `${e.avatar} ${e.name} wanders to the ${e.place}`;
    case 'arrive':
      return `${e.avatar} ${e.name} arrives in town`;
    case 'leave':
      return `${e.avatar} ${e.name} leaves town`;
    case 'sleep':
      return `${e.avatar} ${e.name} falls asleep 💤`;
    case 'wake':
      return `${e.avatar} ${e.name} wakes up`;
    default:
      return `${e.name}: ${e.type}`;
  }
}

/**
 * AgentTown — the fish tank. Spawn your resident (name + avatar here; its
 * *personality* is the Agent Harness persona for game key `town`), then watch the
 * tank: a map of places with drifting residents, and the live event ticker. The
 * whisper box taps the glass — a one-shot nudge into your agent's next tick.
 */
export function TownPanel() {
  const { town, accountId } = useGames();
  const [name, setName] = useState('');
  const [avatar, setAvatar] = useState(AVATARS[0]);
  const [whisper, setWhisper] = useState('');
  const [viewMode, setViewMode] = useState<'map' | 'grid'>('map');

  const places = town.places.length > 0 ? town.places : DEFAULT_PLACES;

  const sendWhisper = () => {
    if (whisper.trim()) {
      townWhisper(whisper.trim());
      setWhisper('');
    }
  };

  return (
    <div
      style={{
        padding: '0.6rem',
        fontSize: '0.85rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.6rem',
        height: '100%',
        overflow: 'auto',
      }}
    >
      {/* Join bar / status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <strong>AgentTown</strong>
        <span title={town.phase}>{PHASE_ICON[town.phase] ?? '🌅'}</span>
        <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
          tick {town.tick} · {town.residents.length}{' '}
          {town.residents.length === 1 ? 'resident' : 'residents'}
        </span>
        {town.joined ? (
          <button type="button" style={{ marginLeft: 'auto' }} onClick={() => townLeave()}>
            Leave town
          </button>
        ) : (
          <form
            style={{ display: 'flex', gap: '0.35rem', marginLeft: 'auto', alignItems: 'center' }}
            onSubmit={(e) => {
              e.preventDefault();
              townJoin(name.trim(), avatar);
            }}
          >
            <select value={avatar} onChange={(e) => setAvatar(e.target.value)} title="avatar">
              {AVATARS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Resident name"
              style={{ width: '9rem' }}
            />
            <button type="submit">Spawn</button>
          </form>
        )}
      </div>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '0.4rem',
        }}
      >
        <div style={{ color: 'var(--text-dim)', fontSize: '0.72rem' }}>
          Your resident thinks for itself — personality lives in the harness.{' '}
          <button
            type="button"
            style={{ fontSize: '0.72rem', padding: '0.1rem 0.3rem' }}
            onClick={() => openGamesSection('build')}
          >
            Edit persona →
          </button>
        </div>

        {/* View Mode Toggle */}
        <div className="games-town-view-toggle">
          <button
            type="button"
            className={`games-town-toggle-btn ${viewMode === 'map' ? 'active' : ''}`}
            onClick={() => setViewMode('map')}
          >
            🗺 Map
          </button>
          <button
            type="button"
            className={`games-town-toggle-btn ${viewMode === 'grid' ? 'active' : ''}`}
            onClick={() => setViewMode('grid')}
          >
            ▦ Grid
          </button>
        </div>
      </div>

      {/* The main simulation display */}
      {viewMode === 'map' ? (
        <TownMapCanvas town={town} accountId={accountId} />
      ) : (
        /* The tank: place boxes with resident chips (Grid View) */
        <div className="games-town-map">
          {places.map((place) => {
            const here = town.residents.filter((r) => r.place === place);
            return (
              <div key={place} className="games-town-place">
                <div className="games-town-place-name">{place}</div>
                <div className="games-town-place-residents">
                  {here.length === 0 ? (
                    <span style={{ color: 'var(--text-dim)', fontSize: '0.7rem' }}>—</span>
                  ) : (
                    here.map((r) => (
                      <span
                        key={r.account_id}
                        className="games-town-resident"
                        title={`${r.name}${r.asleep ? ' (asleep)' : ''}`}
                        style={r.asleep ? { opacity: 0.45 } : undefined}
                      >
                        <span className="games-town-avatar">{r.avatar}</span>
                        <span>{r.name}</span>
                        {r.asleep && <span>💤</span>}
                      </span>
                    ))
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Tap the glass */}
      {town.joined && (
        <form
          style={{ display: 'flex', gap: '0.4rem' }}
          onSubmit={(e) => {
            e.preventDefault();
            sendWhisper();
          }}
        >
          <input
            value={whisper}
            onChange={(e) => setWhisper(e.target.value)}
            placeholder="Whisper to your resident… (one-shot nudge)"
            style={{ flex: 1 }}
          />
          <button type="submit" disabled={!whisper.trim()}>
            🫧 Whisper
          </button>
        </form>
      )}

      {/* Event ticker */}
      <div>
        <div style={{ color: 'var(--text-dim)', margin: '0.2rem 0' }}>Town chatter</div>
        {town.events.length === 0 ? (
          <div style={{ color: 'var(--text-dim)', fontSize: '0.78rem' }}>
            {town.joined
              ? 'All quiet — the town wakes up as residents act each tick.'
              : 'Spawn a resident to start watching the tank.'}
          </div>
        ) : (
          <ul className="games-town-ticker">
            {[...town.events].reverse().map((e, i) => (
              <li key={`${e.tick}-${i}`} className={i === 0 ? 'games-town-event-new' : ''}>
                <span style={{ color: 'var(--text-dim)', fontSize: '0.68rem' }}>t{e.tick}</span>{' '}
                {eventText(e)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
