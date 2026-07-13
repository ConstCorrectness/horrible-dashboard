import { useSetting } from '../../../settings';
import { gamesDisconnect, useGames } from '../game-ws';

/**
 * The connection status, reduced to one glanceable chip. Connecting is implicit
 * (every play/queue/join flow calls `ensureConnected`), so the only control left
 * is a Disconnect tucked into the ⋯ menu for debugging, next to the server URL.
 */
export function ConnectionChip() {
  const { connected, connecting, accountId, selfPlay } = useGames();
  const serverUrl = useSetting<string>('games.serverUrl');

  const label = connecting ? (
    <span style={{ color: 'var(--text-dim)' }}>◌ connecting…</span>
  ) : connected ? (
    <span>
      <span style={{ color: 'var(--ok, #3fb950)' }}>●</span> connected
      {selfPlay ? ' · self-play' : ''}
      {accountId ? (
        // A cross-server token fallback can make the account id a whole JWT —
        // never let the chip balloon.
        <span style={{ color: 'var(--text-dim)' }} title={accountId}>
          {' · '}
          {accountId.length > 24 ? `${accountId.slice(0, 12)}…` : accountId}
        </span>
      ) : null}
    </span>
  ) : (
    <span style={{ color: 'var(--text-dim)' }}>○ connects automatically when you play</span>
  );

  return (
    <span className="games-conn-chip">
      {label}
      {connected && (
        <details className="games-conn-menu">
          <summary title="connection details">⋯</summary>
          <div className="games-conn-menu-body">
            <div style={{ color: 'var(--text-dim)', fontSize: '0.7rem' }}>
              server: <code>{serverUrl || 'default'}</code>
            </div>
            <button type="button" onClick={() => gamesDisconnect()}>
              Disconnect
            </button>
          </div>
        </details>
      )}
    </span>
  );
}
