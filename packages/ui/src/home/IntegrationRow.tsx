import { useEffect, useState } from 'react';
import { onConnectRequested, refreshConnectors, useConnectors } from '@horrible/core';

import { ConnectorIcon } from './connector-icons';
import { ConnectorPopover } from './ConnectorPopover';
import { MobilePairingDialog } from './MobilePairingDialog';

/**
 * The row of integration tiles above the ask bar: what your agent can reach.
 *
 * Renders nothing at all when the backend is down — home already shows one
 * backend-down message and a second would just be noise.
 *
 * State lives in the shared connectors store rather than here. A `requestConnect` from
 * elsewhere in the app is answered by `ConnectorDialog` at the shell level, not here —
 * this row is only mounted on the home surface, and making callers navigate to it was
 * the whole bug. Clicking a tile still opens the popover anchored under that tile.
 */
export function IntegrationRow() {
  const { connectors, phase } = useConnectors();
  const [open, setOpen] = useState<string | null>(null);
  const [showMobile, setShowMobile] = useState(false);

  // A connect request is served by the shell dialog. All this row does is get out of
  // the way, so a tile popover and the dialog are never both open over each other.
  useEffect(
    () =>
      onConnectRequested(() => {
        setOpen(null);
        setShowMobile(false);
      }),
    [],
  );

  if (phase === 'unavailable') return null;

  if (phase === 'loading') {
    // Skeletons at the tiles' final size, so the greeting and ask bar don't jump.
    return (
      <div className="integration-row" aria-busy="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="integration-tile skeleton" />
        ))}
      </div>
    );
  }

  return (
    <div className="integration-row">
      {connectors.map((c) => (
        <div className="integration-slot" key={c.id}>
          <button
            type="button"
            className={`integration-tile${c.connected ? ' connected' : ''}`}
            aria-haspopup="dialog"
            aria-expanded={open === c.id}
            title={
              c.connected ? `${c.label} — ${c.account?.label ?? 'connected'}` : `Connect ${c.label}`
            }
            onClick={() => {
              setOpen(open === c.id ? null : c.id);
              setShowMobile(false);
            }}
          >
            <ConnectorIcon icon={c.icon} label={c.label} />
            {!c.connected && (
              <span className="integration-badge" aria-hidden="true">
                +
              </span>
            )}
            {c.error && <span className="integration-dot warn" aria-hidden="true" />}
          </button>
          <span className="integration-label">{c.label}</span>
          {open === c.id && (
            <ConnectorPopover
              connector={c}
              onClose={() => setOpen(null)}
              onChanged={() => void refreshConnectors()}
            />
          )}
        </div>
      ))}

      {/* Mobile Pairing Tile */}
      <div className="integration-slot">
        <button
          type="button"
          className="integration-tile"
          title="Pair Mobile Device"
          onClick={() => {
            setShowMobile(!showMobile);
            setOpen(null);
          }}
        >
          <span className="integration-letter">📱</span>
        </button>
        <span className="integration-label">Mobile</span>
        {showMobile && <MobilePairingDialog onClose={() => setShowMobile(false)} />}
      </div>
    </div>
  );
}
