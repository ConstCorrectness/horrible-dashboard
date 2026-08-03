import type { ReactNode } from 'react';

import { requestConnect } from './store';
import { useConnector } from './useConnectors';

/**
 * Wrap a pane's body in this when it cannot do anything useful without a
 * connector. Instead of the pane calling the backend, getting a 409, and showing
 * whatever its error path happens to render, the user gets the one thing that
 * actually helps: a button that starts the connect flow.
 *
 * The gate never invents a connect UI of its own — `requestConnect` hands the job
 * to whoever owns the real flow (the home tile row). One implementation, reachable
 * from anywhere.
 *
 * It also renders `children` while the answer is still unknown. Being briefly
 * wrong in the direction of "show the pane" costs a 409 the pane already handles;
 * being wrong the other way flashes a "connect this" screen at users who are
 * already connected, every single mount.
 */
export function ConnectionGate({
  connector,
  label,
  children,
}: {
  /** Connector id — the same string as its agent-tool prefix (`github`). */
  connector: string;
  /** Human name for the prompt. Falls back to the connector's own label. */
  label?: string;
  children: ReactNode;
}) {
  const { connector: found, connected, known } = useConnector(connector);

  if (connected || !known) return <>{children}</>;

  const name = label ?? found?.label ?? connector;

  return (
    <div className="connection-gate">
      <p className="connection-gate-title">Connect {name} to use this</p>
      {found?.blurb && <p className="connection-gate-blurb">{found.blurb}</p>}
      <button type="button" className="primary" onClick={() => requestConnect(connector)}>
        Connect {name}
      </button>
    </div>
  );
}
