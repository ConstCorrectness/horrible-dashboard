import { useState } from 'react';
import { useGameRooms } from '../hooks/useGameRooms';

const BUNDLED_MAPS = [
  { id: 'hd_assault', name: 'CS Assault (Industrial)', mode: 'Deathmatch' },
  { id: 'hd_bank', name: 'Bank Heist (CQC)', mode: 'Deathmatch' },
  { id: 'hd_dust2', name: 'Dust II (Desert Arena)', mode: 'Deathmatch' },
  { id: 'hd_facility', name: 'Research Facility', mode: 'Deathmatch' },
  { id: 'hd_inferno', name: 'Inferno Village', mode: 'Deathmatch' },
  { id: 'hd_nuke', name: 'Nuclear Station', mode: 'Deathmatch' },
];

export interface ServerBrowserProps {
  callsign: string;
  onCallsignChange: (name: string) => void;
  onJoinMatch: (room: string, map: string) => void;
  onHostMatch: (map: string) => void;
}

export function ServerBrowser({
  callsign,
  onCallsignChange,
  onJoinMatch,
  onHostMatch,
}: ServerBrowserProps) {
  const { rooms, loading, pingMs, refresh } = useGameRooms();
  const [editingCallsign, setEditingCallsign] = useState(false);
  const [tempCallsign, setTempCallsign] = useState(callsign);
  const [selectedMap, setSelectedMap] = useState('hd_assault');
  const [showHostModal, setShowHostModal] = useState(false);

  const handleQuickPlay = () => {
    // Find room with highest player count that isn't full (max 16)
    const available = [...rooms].filter((r) => r.playerCount < r.maxPlayers);
    if (available.length > 0) {
      available.sort((a, b) => b.playerCount - a.playerCount);
      onJoinMatch(available[0].id, available[0].map);
    } else {
      // Create new room on hd_assault
      onHostMatch('hd_assault');
    }
  };

  const handleSaveCallsign = () => {
    onCallsignChange(tempCallsign);
    setEditingCallsign(false);
  };

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        overflowY: 'auto',
        backgroundColor: '#070a10',
        backgroundImage: 'radial-gradient(ellipse at 50% 10%, rgba(30, 58, 138, 0.22) 0%, transparent 70%)',
        color: '#f1f5f9',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '2rem 1rem 4rem',
        boxSizing: 'border-box',
      }}
    >
      <div style={{ width: '100%', maxWidth: 960, display: 'flex', flexDirection: 'column', gap: '2rem' }}>
        {/* Top Header */}
        <header
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '1rem',
            borderBottom: '1px solid #1e293b',
            paddingBottom: '1.2rem',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem' }}>
            <div
              style={{
                width: 42,
                height: 42,
                borderRadius: 8,
                backgroundColor: '#0284c7',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '1.5rem',
                boxShadow: '0 0 15px rgba(2, 132, 199, 0.4)',
              }}
            >
              ⌖
            </div>
            <div>
              <h1
                style={{
                  fontSize: '1.4rem',
                  fontWeight: 900,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  color: '#ffffff',
                }}
              >
                Horrible<span style={{ color: '#38bdf8' }}>Assault</span>
              </h1>
              <span style={{ fontSize: '0.75rem', color: '#64748b', fontWeight: 600 }}>
                TACTICAL BROWSER MULTIPLAYER // CROSS-PLAY
              </span>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
            {pingMs !== null && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                  fontSize: '0.75rem',
                  fontWeight: 700,
                  color: pingMs < 80 ? '#10b981' : pingMs < 150 ? '#f59e0b' : '#ef4444',
                  backgroundColor: '#0f172a',
                  padding: '0.35rem 0.7rem',
                  borderRadius: 6,
                  border: '1px solid #1e293b',
                }}
              >
                <span>●</span>
                <span>{pingMs} ms</span>
              </div>
            )}

            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                backgroundColor: '#0f172a',
                padding: '0.35rem 0.8rem',
                borderRadius: 6,
                border: '1px solid #1e293b',
              }}
            >
              <span style={{ fontSize: '0.75rem', color: '#64748b', fontWeight: 600 }}>CALLSIGN:</span>
              {editingCallsign ? (
                <div style={{ display: 'flex', gap: '0.3rem' }}>
                  <input
                    type="text"
                    maxLength={16}
                    value={tempCallsign}
                    onChange={(e) => setTempCallsign(e.target.value)}
                    style={{
                      backgroundColor: '#1e293b',
                      border: '1px solid #38bdf8',
                      color: '#ffffff',
                      borderRadius: 4,
                      padding: '0.1rem 0.4rem',
                      fontSize: '0.8rem',
                      outline: 'none',
                    }}
                    autoFocus
                    onKeyDown={(e) => e.key === 'Enter' && handleSaveCallsign()}
                  />
                  <button
                    type="button"
                    onClick={handleSaveCallsign}
                    style={{
                      background: '#0284c7',
                      border: 'none',
                      color: '#fff',
                      borderRadius: 4,
                      padding: '0.1rem 0.4rem',
                      fontSize: '0.75rem',
                      cursor: 'pointer',
                    }}
                  >
                    ✓
                  </button>
                </div>
              ) : (
                <div
                  onClick={() => {
                    setTempCallsign(callsign);
                    setEditingCallsign(true);
                  }}
                  style={{
                    color: '#38bdf8',
                    fontWeight: 700,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.3rem',
                  }}
                  title="Click to change callsign"
                >
                  <span>{callsign}</span>
                  <span style={{ fontSize: '0.7rem', color: '#64748b' }}>✎</span>
                </div>
              )}
            </div>
          </div>
        </header>

        {/* Quick Play & Action Banner */}
        <div
          style={{
            background: 'linear-gradient(135deg, rgba(14, 116, 144, 0.25) 0%, rgba(2, 132, 199, 0.15) 100%)',
            border: '1px solid rgba(56, 189, 248, 0.3)',
            borderRadius: 12,
            padding: '2rem',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '1.5rem',
            boxShadow: '0 8px 30px rgba(0, 0, 0, 0.4)',
          }}
        >
          <div>
            <span style={{ fontSize: '0.75rem', fontWeight: 800, color: '#38bdf8', letterSpacing: '0.1em' }}>
              MATCHMAKING // CASUAL & UNRATED
            </span>
            <h2 style={{ fontSize: '1.6rem', fontWeight: 900, color: '#f8fafc', marginTop: '0.3rem' }}>
              Instant Battlefield Deployment
            </h2>
            <p style={{ fontSize: '0.85rem', color: '#94a3b8', marginTop: '0.4rem', maxWidth: 500 }}>
              Join live matches with zero wait. Voice chat, spatial tactical radar, weapon recoil, and full hitboxes enabled.
            </p>
          </div>

          <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
            <button
              type="button"
              onClick={handleQuickPlay}
              style={{
                backgroundColor: '#0284c7',
                backgroundImage: 'linear-gradient(180deg, #38bdf8 0%, #0284c7 100%)',
                color: '#ffffff',
                border: 'none',
                borderRadius: 8,
                padding: '0.9rem 1.8rem',
                fontSize: '1rem',
                fontWeight: 900,
                letterSpacing: '0.05em',
                cursor: 'pointer',
                boxShadow: '0 4px 20px rgba(2, 132, 199, 0.5)',
                transition: 'transform 0.1s ease',
              }}
              onMouseDown={(e) => (e.currentTarget.style.transform = 'scale(0.97)')}
              onMouseUp={(e) => (e.currentTarget.style.transform = 'scale(1)')}
            >
              ⚡ QUICK PLAY
            </button>

            <button
              type="button"
              onClick={() => setShowHostModal(true)}
              style={{
                backgroundColor: '#1e293b',
                border: '1px solid #334155',
                color: '#f8fafc',
                borderRadius: 8,
                padding: '0.9rem 1.4rem',
                fontSize: '0.9rem',
                fontWeight: 700,
                cursor: 'pointer',
              }}
            >
              + Create Match
            </button>
          </div>
        </div>

        {/* Server Browser Section */}
        <section style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ fontSize: '1.1rem', fontWeight: 800, color: '#e2e8f0', letterSpacing: '0.04em' }}>
              Active Combat Rooms ({rooms.length})
            </h3>
            <button
              type="button"
              onClick={() => void refresh()}
              style={{
                background: 'transparent',
                border: '1px solid #334155',
                color: '#94a3b8',
                borderRadius: 6,
                padding: '0.3rem 0.7rem',
                fontSize: '0.75rem',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              ↻ Refresh
            </button>
          </div>

          <div
            style={{
              backgroundColor: '#0f172a',
              border: '1px solid #1e293b',
              borderRadius: 8,
              overflow: 'hidden',
            }}
          >
            {loading && rooms.length === 0 ? (
              <div style={{ padding: '3rem', textAlign: 'center', color: '#64748b' }}>
                Scanning battlefield frequencies...
              </div>
            ) : rooms.length === 0 ? (
              <div style={{ padding: '3rem', textAlign: 'center', display: 'flex', flexDirection: 'column', gap: '0.8rem', alignItems: 'center' }}>
                <span style={{ fontSize: '1.8rem', color: '#475569' }}>⚔️</span>
                <span style={{ fontWeight: 700, color: '#94a3b8' }}>No active rooms open right now.</span>
                <span style={{ fontSize: '0.85rem', color: '#64748b' }}>
                  Hit <strong>Quick Play</strong> above to open the first match on <code>hd_assault</code>!
                </span>
              </div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.85rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #1e293b', color: '#64748b', fontSize: '0.75rem', textTransform: 'uppercase' }}>
                    <th style={{ padding: '0.8rem 1rem' }}>Map</th>
                    <th style={{ padding: '0.8rem 1rem' }}>Room ID</th>
                    <th style={{ padding: '0.8rem 1rem' }}>Mode</th>
                    <th style={{ padding: '0.8rem 1rem' }}>Players</th>
                    <th style={{ padding: '0.8rem 1rem', textAlign: 'right' }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {rooms.map((room) => (
                    <tr
                      key={room.id}
                      style={{
                        borderBottom: '1px solid #1e293b',
                        transition: 'background-color 0.15s ease',
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.03)')}
                      onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
                    >
                      <td style={{ padding: '0.8rem 1rem', fontWeight: 700, color: '#38bdf8' }}>
                        {room.map}
                      </td>
                      <td style={{ padding: '0.8rem 1rem', fontFamily: 'monospace', color: '#94a3b8' }}>
                        {room.id}
                      </td>
                      <td style={{ padding: '0.8rem 1rem', color: '#cbd5e1' }}>
                        {room.mode.toUpperCase()}
                      </td>
                      <td style={{ padding: '0.8rem 1rem' }}>
                        <span style={{ fontWeight: 700, color: '#f8fafc' }}>{room.playerCount}</span>
                        <span style={{ color: '#64748b' }}> / {room.maxPlayers}</span>
                      </td>
                      <td style={{ padding: '0.8rem 1rem', textAlign: 'right' }}>
                        <button
                          type="button"
                          onClick={() => onJoinMatch(room.id, room.map)}
                          style={{
                            backgroundColor: '#0284c7',
                            color: '#ffffff',
                            border: 'none',
                            borderRadius: 4,
                            padding: '0.4rem 0.9rem',
                            fontWeight: 700,
                            fontSize: '0.78rem',
                            cursor: 'pointer',
                          }}
                        >
                          JOIN
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>

        {/* Create Match Modal */}
        {showHostModal && (
          <div
            style={{
              position: 'fixed',
              inset: 0,
              backgroundColor: 'rgba(0,0,0,0.8)',
              backdropFilter: 'blur(4px)',
              zIndex: 10000,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '1rem',
            }}
            onClick={() => setShowHostModal(false)}
          >
            <div
              style={{
                width: '100%',
                maxWidth: 480,
                backgroundColor: '#0f172a',
                border: '1px solid #334155',
                borderRadius: 10,
                padding: '1.8rem',
                boxShadow: '0 20px 50px rgba(0,0,0,0.7)',
                display: 'flex',
                flexDirection: 'column',
                gap: '1.2rem',
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h3 style={{ fontSize: '1.2rem', fontWeight: 800, color: '#f8fafc' }}>
                  Create Custom Match
                </h3>
                <button
                  type="button"
                  onClick={() => setShowHostModal(false)}
                  style={{ background: 'transparent', border: 'none', color: '#94a3b8', fontSize: '1.2rem', cursor: 'pointer' }}
                >
                  ✕
                </button>
              </div>

              <div>
                <label style={{ display: 'block', fontSize: '0.82rem', fontWeight: 600, color: '#94a3b8', marginBottom: '0.5rem' }}>
                  Select Map:
                </label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                  {BUNDLED_MAPS.map((m) => (
                    <div
                      key={m.id}
                      onClick={() => setSelectedMap(m.id)}
                      style={{
                        padding: '0.6rem 0.8rem',
                        borderRadius: 6,
                        border: selectedMap === m.id ? '1px solid #38bdf8' : '1px solid #1e293b',
                        backgroundColor: selectedMap === m.id ? 'rgba(56, 189, 248, 0.1)' : '#090d16',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        cursor: 'pointer',
                      }}
                    >
                      <span style={{ fontWeight: 700, color: selectedMap === m.id ? '#38bdf8' : '#cbd5e1' }}>
                        {m.name}
                      </span>
                      <span style={{ fontSize: '0.75rem', color: '#64748b' }}>{m.id}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ display: 'flex', gap: '0.8rem', marginTop: '0.6rem' }}>
                <button
                  type="button"
                  onClick={() => setShowHostModal(false)}
                  style={{
                    flex: 1,
                    backgroundColor: '#1e293b',
                    border: '1px solid #334155',
                    color: '#cbd5e1',
                    borderRadius: 6,
                    padding: '0.7rem',
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowHostModal(false);
                    onHostMatch(selectedMap);
                  }}
                  style={{
                    flex: 2,
                    backgroundColor: '#0284c7',
                    color: '#ffffff',
                    border: 'none',
                    borderRadius: 6,
                    padding: '0.7rem',
                    fontWeight: 800,
                    cursor: 'pointer',
                  }}
                >
                  Launch Match
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
