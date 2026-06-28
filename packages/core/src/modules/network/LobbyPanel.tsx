import { useEffect, useState, useSyncExternalStore } from 'react';

import { dialogs } from '../../dialogs';
import {
  getLobbyState,
  initLobby,
  lobbyConnect,
  lobbyCreateRoom,
  lobbyJoinRoom,
  lobbyListRooms,
  subscribeLobby,
} from './lobby';

function useLobby() {
  return useSyncExternalStore(subscribeLobby, getLobbyState, getLobbyState);
}

/**
 * The lobby: discover hosted rooms (named sessions) and join one — the lobby hands
 * off to a direct P2P link (relay fallback) so the rest flows node-to-node. Connect
 * to a lobby server (default from the `network.lobbyUrl` setting), host a room, or
 * join a listed one. See docs/architecture/network-protocol.mdx (the lobby system).
 */
export function LobbyPanel() {
  const { connected, url, rooms } = useLobby();
  const [roomName, setRoomName] = useState('');

  useEffect(() => {
    initLobby();
    lobbyListRooms();
  }, []);

  return (
    <div
      className="lobby-widget"
      style={{
        padding: '1rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '1rem',
        height: '100%',
        overflow: 'auto',
      }}
    >
      <section style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <span
          aria-label={connected ? 'connected' : 'disconnected'}
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: connected ? '#3fb950' : 'var(--text-dim)',
          }}
        />
        <strong>{connected ? 'Lobby connected' : 'Lobby offline'}</strong>
        {url && <code style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>{url}</code>}
        {!connected && (
          <button style={{ marginLeft: 'auto' }} onClick={() => lobbyConnect()}>
            Connect
          </button>
        )}
      </section>

      {connected && (
        <section style={{ display: 'flex', gap: '0.5rem' }}>
          <form
            style={{ display: 'flex', gap: '0.5rem', flex: 1 }}
            onSubmit={(e) => {
              e.preventDefault();
              if (roomName.trim()) {
                lobbyCreateRoom(roomName.trim());
                setRoomName('');
              }
            }}
          >
            <input
              value={roomName}
              placeholder="Host a room…"
              onChange={(e) => setRoomName(e.target.value)}
              style={{ flex: 1 }}
            />
            <button type="submit" disabled={!roomName.trim()}>
              Host
            </button>
          </form>
          <button onClick={() => lobbyListRooms()} title="Refresh">
            ↻
          </button>
        </section>
      )}

      <section>
        <h3 style={{ margin: '0 0 0.5rem' }}>Rooms ({rooms.length})</h3>
        {rooms.length === 0 ? (
          <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>
            {connected ? 'No rooms yet — host one above.' : 'Connect to a lobby to see rooms.'}
          </p>
        ) : (
          <ul
            style={{
              listStyle: 'none',
              margin: 0,
              padding: 0,
              display: 'flex',
              flexDirection: 'column',
              gap: '0.4rem',
            }}
          >
            {rooms.map((r) => (
              <li
                key={r.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  fontSize: '0.85rem',
                }}
              >
                <strong style={{ color: 'var(--text)' }}>{r.name}</strong>
                <span style={{ color: 'var(--text-dim)' }}>
                  by {r.host_name} · {r.members} {r.members === 1 ? 'member' : 'members'}
                </span>
                {r.locked && <span title="join policy: locked">🔒</span>}
                <button
                  style={{ marginLeft: 'auto' }}
                  onClick={() => {
                    if (!r.locked) {
                      lobbyJoinRoom(r.id, undefined);
                      return;
                    }
                    void dialogs
                      .prompt({
                        title: 'Join locked room',
                        message: `“${r.name}” requires a token to join.`,
                        placeholder: 'Room token',
                        confirmLabel: 'Join',
                      })
                      .then((token) => {
                        if (token) lobbyJoinRoom(r.id, token);
                      });
                  }}
                >
                  Join
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
