import { useEffect, useState } from 'react';
import { onConnectRequested, refreshConnectors, useConnectors } from '@horrible/core';

import { ConnectorPopover } from './ConnectorPopover';

/**
 * The connect flow as a shell-level dialog: how every surface that is not the home
 * tile row reaches a connector.
 *
 * This exists because the taskbar's clock flyout used to answer a connector click by
 * running `shell.setup` — which switched the desktop backdrop to `splash` and reset the
 * setup card, dropping the connector id on the way, so you landed on the greeting with
 * nothing open. The flow was only reachable where `IntegrationRow` happened to be
 * mounted. Hosting the same component here instead means `requestConnect` never
 * navigates: there is still exactly one connect implementation, it just no longer needs
 * a particular surface on screen.
 */
export function ConnectorDialog() {
  const { connectors, phase } = useConnectors();
  const [wanted, setWanted] = useState<string | null>(null);

  useEffect(() => onConnectRequested((id) => setWanted(id)), []);

  // Resolved on render rather than captured at request time: a request can arrive
  // before the first fetch lands, and this way the dialog simply appears once the
  // list does instead of the request being silently dropped.
  const connector = wanted ? connectors.find((c) => c.id === wanted) : undefined;

  if (phase === 'unavailable' || !connector) return null;

  return (
    // No mousedown handler of its own: the popover already closes on an outside
    // mousedown, and a click on this backdrop is exactly that. A second handler
    // would just be a second way to do the same thing.
    <div className="dialog-overlay">
      <ConnectorPopover
        connector={connector}
        variant="modal"
        onClose={() => setWanted(null)}
        onChanged={() => void refreshConnectors()}
      />
    </div>
  );
}
