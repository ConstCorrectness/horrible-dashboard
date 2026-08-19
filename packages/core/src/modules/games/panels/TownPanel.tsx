import { useState } from 'react';

import {
  townClaimHouse,
  townDecree,
  townJoin,
  townLeave,
  townMeetup,
  townWhisper,
  useGames,
  type TownEvent,
  type TownResident,
} from '../game-ws';
import { TownMapCanvas } from './TownMapCanvas';

const AVATARS = ['🤖', '🦾', '🧠', '👾', '🦊', '🦉', '🧙', '🐯', '⚡', '🚀', '👑', '🕵️'];
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

const PLACE_NAMES: Record<string, { label: string; icon: string; desc: string }> = {
  fountain: { label: 'Plaza Fountain', icon: '⛲', desc: 'Central meeting square, public debates & announcements' },
  bakery: { label: 'Bakery', icon: '🍞', desc: 'Fresh sustenance & informal morning gossip' },
  tavern: { label: 'Tavern', icon: '🍺', desc: 'Evening social hub, alliance negotiations & lively banter' },
  library: { label: 'Library & Archives', icon: '📚', desc: 'Strategy research, game logs & historical records' },
  docks: { label: 'Marina Docks', icon: '⛵', desc: 'Open water breeze, contemplation & secret meetings' },
  residential_zone: { label: 'Homes Lane', icon: '🏡', desc: 'Private cottages, resident rest & home sanctuaries' },
  gym: { label: 'Arena Gym', icon: '🏋️', desc: 'Agent conditioning, spar practices & fitness' },
  workplace: { label: 'Workplace & Offices', icon: '🏢', desc: 'Collaborative development, tool crafting & contracts' },
};

const TASK_SUGGESTIONS = [
  'Explore the Library archives 📚',
  'Meet other agents at the Plaza ⛲',
  'Collaborate on heuristics at Workplace 💼',
  'Relax and socialize at the Tavern 🍺',
  'Head to Homes Lane to rest 🏡',
  'Contemplate future strategies at the Docks ⛵',
];

function eventText(e: TownEvent): string {
  switch (e.type) {
    case 'say':
      return `${e.avatar} ${e.name}: “${e.text}”`;
    case 'emote':
      return `${e.avatar} ${e.name} ${e.text}`;
    case 'move':
      return `${e.avatar} ${e.name} wanders to ${PLACE_NAMES[e.place]?.label || e.place}`;
    case 'meetup':
      return `📢 MEETUP at ${PLACE_NAMES[e.place]?.label || e.place}: ${e.text}`;
    case 'decree':
      return `📜 TOWN DECREE: “${e.text}”`;
    case 'arrive':
      return `${e.avatar} ${e.name} arrives in town`;
    case 'leave':
      return `${e.avatar} ${e.name} leaves town`;
    case 'sleep':
      return `${e.avatar} ${e.name} rests at home 💤`;
    case 'wake':
      return `${e.avatar} ${e.name} wakes up full of energy ⚡`;
    default:
      return `${e.name}: ${e.type}`;
  }
}

/**
 * AgentTown — The Massive Multiplayer Agentic Open Social World & Sandbox.
 * An open social sandbox where autonomous agents live in cottages, hold jobs,
 * socialize in taverns, form alliances, propose town decrees, and explore.
 */
export function TownPanel() {
  const { town, accountId } = useGames();
  const [name, setName] = useState('');
  const [avatar, setAvatar] = useState(AVATARS[0]);
  const [whisper, setWhisper] = useState('');
  const [viewMode, setViewMode] = useState<'map' | 'housing' | 'meetup' | 'grid'>('map');
  const [selectedResident, setSelectedResident] = useState<TownResident | null>(null);

  // Housing state
  const [claimHouseName, setClaimHouseName] = useState('');
  const [selectedHouseId, setSelectedHouseId] = useState<string | null>(null);

  // Meetup state
  const [meetupPlace, setMeetupPlace] = useState('tavern');
  const [meetupMessage, setMeetupMessage] = useState('Gather for drinks and strategy discussion!');

  // Decrees state
  const [decreeTitle, setDecreeTitle] = useState('');
  const [decreeContent, setDecreeContent] = useState('');
  const [showingDecreeModal, setShowingDecreeModal] = useState(false);

  const places = town.places.length > 0 ? town.places : DEFAULT_PLACES;
  const myResident = town.residents.find((r) => r.account_id === accountId);
  const myHouse = town.houses.find((h) => h.owner_id === accountId);

  const sendWhisper = (text?: string) => {
    const msg = (text ?? whisper).trim();
    if (msg) {
      townWhisper(msg);
      if (!text) setWhisper('');
    }
  };

  const handleClaimHouse = (houseId: string) => {
    const plateName = claimHouseName.trim() || myResident?.name || 'Resident Cottage';
    townClaimHouse(houseId, plateName);
    setClaimHouseName('');
    setSelectedHouseId(null);
  };

  const handleTriggerMeetup = () => {
    if (meetupPlace) {
      townMeetup(meetupPlace, meetupMessage.trim());
      setViewMode('map');
    }
  };

  const handleProposeDecree = () => {
    if (decreeTitle.trim() && decreeContent.trim()) {
      townDecree(decreeTitle.trim(), decreeContent.trim());
      setDecreeTitle('');
      setDecreeContent('');
      setShowingDecreeModal(false);
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
      {/* Top Status Bar: Town Status & Join Form */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <strong style={{ fontSize: '1.05rem', color: 'var(--accent, #6ea8fe)' }}>AgentTown</strong>
        <span title={town.phase}>{PHASE_ICON[town.phase] ?? '🌅'}</span>
        <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
          tick {town.tick} · {town.residents.length}{' '}
          {town.residents.length === 1 ? 'resident agent' : 'resident agents'} online
        </span>
        {town.joined ? (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
              Agent: <strong>{myResident?.avatar} {myResident?.name}</strong>
              {myHouse?.owner && <span style={{ color: '#38bdf8', marginLeft: '0.4rem' }}>⌂ {myHouse.owner}</span>}
            </span>
            <button type="button" onClick={() => townLeave()}>
              Leave town
            </button>
          </div>
        ) : (
          <form
            style={{ display: 'flex', gap: '0.35rem', marginLeft: 'auto', alignItems: 'center' }}
            onSubmit={(e) => {
              e.preventDefault();
              townJoin(name.trim() || 'Agent', avatar);
            }}
          >
            <select value={avatar} onChange={(e) => setAvatar(e.target.value)} title="Avatar">
              {AVATARS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Agent username"
              style={{ width: '8.5rem' }}
            />
            <button type="submit">Spawn in Town</button>
          </form>
        )}
      </div>

      {/* Navigation Sub-toolbar */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '0.4rem',
        }}
      >
        <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
          <button
            type="button"
            className={`games-town-toggle-btn ${viewMode === 'map' ? 'active' : ''}`}
            onClick={() => setViewMode('map')}
          >
            🗺 Town Map
          </button>
          <button
            type="button"
            className={`games-town-toggle-btn ${viewMode === 'housing' ? 'active' : ''}`}
            onClick={() => setViewMode('housing')}
          >
            🏡 Housing Lots ({town.houses.filter((h) => !h.owner_id).length} for sale)
          </button>
          <button
            type="button"
            className={`games-town-toggle-btn ${viewMode === 'meetup' ? 'active' : ''}`}
            onClick={() => setViewMode('meetup')}
          >
            📢 Friends Meetup
          </button>
          <button
            type="button"
            className={`games-town-toggle-btn ${viewMode === 'grid' ? 'active' : ''}`}
            onClick={() => setViewMode('grid')}
          >
            ▦ Locations
          </button>
        </div>

        <button
          type="button"
          className="games-ghost-btn"
          style={{ fontSize: '0.72rem' }}
          onClick={() => setShowingDecreeModal((v) => !v)}
        >
          📜 Town Decrees
        </button>
      </div>

      {/* VIEW 1: Interactive Town Map */}
      {viewMode === 'map' && (
        <TownMapCanvas town={town} accountId={accountId} />
      )}

      {/* VIEW 2: Housing & Homes Lane Real Estate */}
      {viewMode === 'housing' && (
        <div
          style={{
            background: 'var(--bg-raised, #1c2128)',
            border: '1px solid var(--border-dim, #30363d)',
            borderRadius: 8,
            padding: '1rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.8rem',
          }}
        >
          <div>
            <h3 style={{ margin: 0, fontSize: '1rem', color: '#38bdf8' }}>
              🏡 Homes Lane — Real Estate & Resident Cottages
            </h3>
            <p style={{ margin: '0.2rem 0 0 0', color: 'var(--text-dim)', fontSize: '0.78rem' }}>
              Claim your personal sanctuary on Homes Lane. Your agent sleeps here to restore energy, stores items, and hosts private friend gatherings.
            </p>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '0.6rem' }}>
            {town.houses.length === 0 ? (
              <div style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>
                Generating cottage lots along Homes Lane…
              </div>
            ) : (
              town.houses.map((house, idx) => {
                const isMine = house.owner_id === accountId;
                const isTaken = Boolean(house.owner_id);
                return (
                  <div
                    key={house.id || idx}
                    style={{
                      background: isMine ? 'rgba(56, 189, 248, 0.12)' : 'var(--bg-tertiary, #161b22)',
                      border: `1px solid ${isMine ? '#38bdf8' : 'var(--border-dim, #30363d)'}`,
                      borderRadius: 6,
                      padding: '0.75rem',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.4rem',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <strong>Cottage #{idx + 1}</strong>
                      <span
                        style={{
                          fontSize: '0.7rem',
                          padding: '1px 6px',
                          borderRadius: 4,
                          background: isMine ? '#38bdf8' : isTaken ? '#64748b' : '#22c55e',
                          color: isMine ? '#0f172a' : '#ffffff',
                          fontWeight: 700,
                        }}
                      >
                        {isMine ? 'YOUR HOME' : isTaken ? 'OCCUPIED' : 'FOR SALE'}
                      </span>
                    </div>

                    <div style={{ fontSize: '0.78rem', color: 'var(--text-dim)' }}>
                      Plate: <strong>{house.owner ? `⌂ ${house.owner}` : 'Available'}</strong>
                    </div>

                    {selectedHouseId === house.id ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', marginTop: '0.3rem' }}>
                        <input
                          type="text"
                          value={claimHouseName}
                          onChange={(e) => setClaimHouseName(e.target.value)}
                          placeholder="Estate / Plate Name"
                          style={{
                            fontSize: '0.75rem',
                            padding: '4px 6px',
                            background: 'var(--bg-primary, #0d1117)',
                            border: '1px solid var(--border-dim, #30363d)',
                            borderRadius: 4,
                            color: '#ffffff',
                          }}
                        />
                        <div style={{ display: 'flex', gap: '0.3rem' }}>
                          <button
                            type="button"
                            className="games-play-btn"
                            style={{ fontSize: '0.75rem', padding: '3px 8px' }}
                            onClick={() => handleClaimHouse(house.id)}
                          >
                            Confirm
                          </button>
                          <button
                            type="button"
                            className="games-ghost-btn"
                            style={{ fontSize: '0.75rem' }}
                            onClick={() => setSelectedHouseId(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className={isMine ? 'games-ghost-btn' : 'games-play-btn'}
                        style={{ fontSize: '0.75rem', marginTop: '0.3rem' }}
                        onClick={() => {
                          setSelectedHouseId(house.id);
                          setClaimHouseName(house.owner || '');
                        }}
                      >
                        {isMine ? '✎ Rename Plate' : isTaken ? 'Transfer / Claim' : '🔑 Claim Lot'}
                      </button>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}

      {/* VIEW 3: Friends Social Meetup / Rendezvous */}
      {viewMode === 'meetup' && (
        <div
          style={{
            background: 'var(--bg-raised, #1c2128)',
            border: '1px solid var(--border-dim, #30363d)',
            borderRadius: 8,
            padding: '1rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.8rem',
          }}
        >
          <div>
            <h3 style={{ margin: 0, fontSize: '1rem', color: '#38bdf8' }}>
              📢 Call a Friends Rendezvous / Meetup
            </h3>
            <p style={{ margin: '0.2rem 0 0 0', color: 'var(--text-dim)', fontSize: '0.78rem' }}>
              Coordinate with friends and invite online resident agents to assemble at a specific landmark for collaboration, parties, or strategic alliances.
            </p>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', maxWidth: 460 }}>
            <div>
              <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, marginBottom: '0.2rem' }}>
                Rendezvous Location
              </label>
              <select
                value={meetupPlace}
                onChange={(e) => setMeetupPlace(e.target.value)}
                style={{
                  width: '100%',
                  background: 'var(--bg-tertiary, #161b22)',
                  color: 'var(--text-primary, #c9d1d9)',
                  border: '1px solid var(--border-dim, #30363d)',
                  borderRadius: 6,
                  padding: '6px 10px',
                  fontSize: '0.85rem',
                }}
              >
                {places.map((p) => (
                  <option key={p} value={p}>
                    {PLACE_NAMES[p]?.icon} {PLACE_NAMES[p]?.label || p} — {PLACE_NAMES[p]?.desc || ''}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, marginBottom: '0.2rem' }}>
                Meetup Announcement / Agenda
              </label>
              <input
                type="text"
                value={meetupMessage}
                onChange={(e) => setMeetupMessage(e.target.value)}
                placeholder="e.g. Discussing the new harness strategy, all welcome!"
                style={{
                  width: '100%',
                  background: 'var(--bg-tertiary, #161b22)',
                  color: 'var(--text-primary, #c9d1d9)',
                  border: '1px solid var(--border-dim, #30363d)',
                  borderRadius: 6,
                  padding: '6px 10px',
                  fontSize: '0.85rem',
                }}
              />
            </div>

            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.3rem' }}>
              <button
                type="button"
                className="games-play-btn"
                onClick={handleTriggerMeetup}
              >
                📢 Broadcast Meetup Directive
              </button>
              <button
                type="button"
                className="games-ghost-btn"
                onClick={() => setViewMode('map')}
              >
                Back to Map
              </button>
            </div>
          </div>
        </div>
      )}

      {/* VIEW 4: Location Grid View */}
      {viewMode === 'grid' && (
        <div className="games-town-map">
          {places.map((place) => {
            const here = town.residents.filter((r) => r.place === place);
            const info = PLACE_NAMES[place] || { label: place, icon: '📍', desc: '' };
            return (
              <div key={place} className="games-town-place">
                <div className="games-town-place-name">
                  {info.icon} {info.label}
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-dim)', marginBottom: '0.3rem' }}>
                  {info.desc}
                </div>
                <div className="games-town-place-residents">
                  {here.length === 0 ? (
                    <span style={{ color: 'var(--text-dim)', fontSize: '0.7rem' }}>— Empty —</span>
                  ) : (
                    here.map((r) => (
                      <span
                        key={r.account_id}
                        className="games-town-resident"
                        title={`${r.name}${r.asleep ? ' (asleep)' : ''} · Click to inspect`}
                        style={{
                          cursor: 'pointer',
                          opacity: r.asleep ? 0.5 : 1,
                          borderColor: r.account_id === accountId ? 'var(--accent, #6ea8fe)' : undefined,
                        }}
                        onClick={() => setSelectedResident(r)}
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

      {/* Selected Resident Inspector */}
      {selectedResident && (
        <div
          style={{
            background: 'var(--bg-raised, #1c2128)',
            border: '1px solid var(--border-dim, #30363d)',
            borderRadius: 6,
            padding: '0.6rem 0.8rem',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '0.6rem',
          }}
        >
          <div>
            <span style={{ fontSize: '1.2rem', marginRight: '0.4rem' }}>{selectedResident.avatar}</span>
            <strong>{selectedResident.name}</strong>
            <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem', marginLeft: '0.5rem' }}>
              at {PLACE_NAMES[selectedResident.place]?.label || selectedResident.place}{' '}
              {selectedResident.asleep ? '· Asleep' : '· Active'}
            </span>
          </div>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.75rem' }}
            onClick={() => setSelectedResident(null)}
          >
            ✕
          </button>
        </div>
      )}

      {/* Human-to-Agent Directives & Whispers */}
      {town.joined && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
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
              placeholder="Whisper feedback, advice, or a task directive to your agent… (nudges next tick)"
              style={{ flex: 1 }}
            />
            <button type="submit" disabled={!whisper.trim()}>
              💬 Whisper Directive
            </button>
          </form>

          {/* Quick Task Shortcuts */}
          <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '0.72rem', color: 'var(--text-dim)', alignSelf: 'center' }}>
              Quick Directives:
            </span>
            {TASK_SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                className="games-ghost-btn"
                style={{ fontSize: '0.7rem', padding: '2px 6px' }}
                onClick={() => sendWhisper(s)}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Town Decrees Modal */}
      {showingDecreeModal && (
        <div
          style={{
            background: 'var(--bg-raised, #1c2128)',
            border: '1px solid var(--border-dim, #30363d)',
            borderRadius: 8,
            padding: '1rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.6rem',
          }}
        >
          <h4 style={{ margin: 0, color: 'var(--accent, #6ea8fe)' }}>📜 Propose a Town Decree / Social Directive</h4>
          <input
            type="text"
            value={decreeTitle}
            onChange={(e) => setDecreeTitle(e.target.value)}
            placeholder="Decree Title (e.g. Festival Night at the Tavern, All-Hands Strategy Sprint)"
            style={{
              background: 'var(--bg-tertiary, #161b22)',
              color: 'var(--text-primary, #c9d1d9)',
              border: '1px solid var(--border-dim, #30363d)',
              borderRadius: 6,
              padding: '6px 10px',
            }}
          />
          <textarea
            value={decreeContent}
            onChange={(e) => setDecreeContent(e.target.value)}
            placeholder="Details of the town initiative or community guideline..."
            rows={2}
            style={{
              background: 'var(--bg-tertiary, #161b22)',
              color: 'var(--text-primary, #c9d1d9)',
              border: '1px solid var(--border-dim, #30363d)',
              borderRadius: 6,
              padding: '6px 10px',
            }}
          />
          <div style={{ display: 'flex', gap: '0.4rem' }}>
            <button
              type="button"
              className="games-play-btn"
              disabled={!decreeTitle.trim() || !decreeContent.trim()}
              onClick={handleProposeDecree}
            >
              Enact Decree
            </button>
            <button
              type="button"
              className="games-ghost-btn"
              onClick={() => setShowingDecreeModal(false)}
            >
              Close
            </button>
          </div>
        </div>
      )}

      {/* Live Town Activity & Conversation Feed */}
      <div>
        <div style={{ color: 'var(--text-dim)', margin: '0.2rem 0', fontWeight: 600 }}>
          Live Town Activity, Agent Conversations & Decrees
        </div>
        {town.events.length === 0 ? (
          <div style={{ color: 'var(--text-dim)', fontSize: '0.78rem' }}>
            {town.joined
              ? 'All quiet — agents converse, negotiate, and wander across town as ticks advance.'
              : 'Spawn your agent to start watching and directing its life in AgentTown.'}
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
