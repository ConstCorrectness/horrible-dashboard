import { useEffect, useState } from 'react';
import { listConnectors, type Connector } from '@horrible/core';

import { ConnectorIcon } from './connector-icons';
import { ConnectorPopover } from './ConnectorPopover';
import { MobilePairingDialog } from './MobilePairingDialog';

/**
 * The row of integration tiles above the ask bar: what your agent can reach.
 *
 * Renders nothing at all when the backend is down — home already shows one
 * backend-down message and a second would just be noise.
 */
export function IntegrationRow() {
  const [connectors, setConnectors] = useState<Connector[] | 'loading' | 'unavailable'>('loading');
  const [open, setOpen] = useState<string | null>(null);
  const [showMobile, setShowMobile] = useState(false);

  const refresh = () =>
    listConnectors()
      .then(setConnectors)
      .catch(() => setConnectors('unavailable'));

  useEffect(() => {
    void refresh();
  }, []);

  if (connectors === 'unavailable') return null;

  if (connectors === 'loading') {
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
              onChanged={() => void refresh()}
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
