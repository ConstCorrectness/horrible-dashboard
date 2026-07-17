import { useEffect, useState } from 'react';
import { listConnectors, type Connector } from '@horrible/core';

import { ConnectorIcon } from './connector-icons';
import { ConnectorPopover } from './ConnectorPopover';

/**
 * The row of integration tiles above the ask bar: what your agent can reach.
 *
 * Renders nothing at all when the backend is down — home already shows one
 * backend-down message and a second would just be noise.
 */
export function IntegrationRow() {
  const [connectors, setConnectors] = useState<Connector[] | 'loading' | 'unavailable'>('loading');
  const [open, setOpen] = useState<string | null>(null);

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
        {[0, 1, 2].map((i) => (
          <div key={i} className="integration-tile skeleton" />
        ))}
      </div>
    );
  }

  if (connectors.length === 0) return null;

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
            onClick={() => setOpen(open === c.id ? null : c.id)}
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
    </div>
  );
}
