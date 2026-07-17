/**
 * Client for the connectors surface (`/api/connectors`).
 *
 * A connector is one external account the node holds credentials for. The token
 * itself never crosses this boundary — the backend holds it and these calls only ever
 * learn *that* an account is connected. Types mirror
 * `backend/modules/connectors/models.py`, which is the source of truth.
 */
import { apiDelete, apiGet, apiPost } from '../api';

export type ConnectorKind = 'oauth' | 'api-key' | 'custom';

export interface ConnectorScope {
  id: string;
  label: string;
  description: string;
}

export interface ConnectorAccount {
  id: string;
  label: string;
  avatar_url: string | null;
}

export interface Connector {
  id: string;
  label: string;
  kind: ConnectorKind;
  /** Icon slug; unknown slugs fall back to a letter avatar. */
  icon: string;
  blurb: string;
  connected: boolean;
  account: ConnectorAccount | null;
  /** What the connector *asks* for. */
  scopes: ConnectorScope[];
  /** What the provider actually granted. */
  granted_scopes: string[];
  /** Set when a connection exists but is unusable — distinct from `!connected`. */
  error: string | null;
}

export interface ConnectorField {
  name: string;
  label: string;
  secret: boolean;
  placeholder: string;
}

/** One step of a connect flow. A single shape covers all three connector kinds. */
export interface ConnectStep {
  step: 'device' | 'redirect' | 'form' | null;
  user_code: string | null;
  verification_uri: string | null;
  interval: number | null;
  expires_in: number | null;
  authorize_url: string | null;
  fields: ConnectorField[];
  connected: boolean;
  account: ConnectorAccount | null;
  pending: boolean;
  error: string | null;
}

export function listConnectors(): Promise<Connector[]> {
  return apiGet<{ connectors: Connector[] }>('/connectors').then((r) => r.connectors);
}

export function beginConnect(
  id: string,
  options: Record<string, unknown> = {},
): Promise<ConnectStep> {
  return apiPost<ConnectStep>(`/connectors/${encodeURIComponent(id)}/connect`, { options });
}

export function submitConnect(id: string, values: Record<string, string>): Promise<ConnectStep> {
  return apiPost<ConnectStep>(`/connectors/${encodeURIComponent(id)}/submit`, { values });
}

export function pollConnect(id: string): Promise<ConnectStep> {
  return apiPost<ConnectStep>(`/connectors/${encodeURIComponent(id)}/poll`, {});
}

export function disconnectConnector(id: string): Promise<Connector> {
  return apiDelete<Connector>(`/connectors/${encodeURIComponent(id)}`);
}

const DEFAULT_INTERVAL_S = 2;

export interface PollOptions {
  /** Seconds between polls. Device flows get this from the provider; going faster
   * earns a `slow_down`. */
  intervalS?: number;
  /** Give up after this long, matching the flow's server-side TTL. */
  expiresInS?: number;
  signal?: AbortSignal;
  /** Injectable for tests; defaults to real time. */
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
}

const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Poll a connect flow until it resolves.
 *
 * Stops at `expiresInS` rather than polling forever: the backend forgets the flow at
 * its TTL, so a client that kept going would spin against a permanent error. A
 * transport failure is treated as transient and retried until the deadline — the
 * backend can restart mid-flow.
 */
export async function pollUntilDone(id: string, options: PollOptions = {}): Promise<ConnectStep> {
  const now = options.now ?? (() => Date.now());
  const sleep = options.sleep ?? realSleep;
  const intervalMs = (options.intervalS ?? DEFAULT_INTERVAL_S) * 1000;
  const deadline = now() + (options.expiresInS ?? 900) * 1000;

  for (;;) {
    if (options.signal?.aborted) return { ...emptyStep(), error: 'cancelled' };
    if (now() >= deadline) return { ...emptyStep(), error: 'sign-in timed out — start again' };

    await sleep(intervalMs);
    if (options.signal?.aborted) return { ...emptyStep(), error: 'cancelled' };

    try {
      const step = await pollConnect(id);
      if (step.connected || step.error) return step;
    } catch {
      // Transient: the backend may be restarting. Keep trying until the deadline.
    }
  }
}

function emptyStep(): ConnectStep {
  return {
    step: null,
    user_code: null,
    verification_uri: null,
    interval: null,
    expires_in: null,
    authorize_url: null,
    fields: [],
    connected: false,
    account: null,
    pending: false,
    error: null,
  };
}
