/**
 * HTTP client for the OTLP side of trajectories (`backend/modules/otel/`).
 *
 * Types mirror `backend/modules/otel/models.py`.
 *
 * `start_ns`/`end_ns` arrive as JSON numbers. Epoch nanoseconds (~1.8e18) exceed
 * 2^53, so the browser holds them to within a few hundred nanoseconds — invisible
 * on any bar a person can see, and not worth a bigint for a drawing.
 */
import type { TrajectoryRun } from './api';

export interface OtelSpanEvent {
  name: string;
  time_ns: number;
  attrs: Record<string, unknown>;
}

export interface OtelSpan {
  trace_id: string;
  span_id: string;
  parent_span_id: string;
  name: string;
  kind: number;
  start_ns: number;
  end_ns: number;
  /** OTLP status: 0 unset, 1 ok, 2 error. */
  status_code: number;
  status_message: string;
  attrs: Record<string, unknown>;
  events: OtelSpanEvent[];
  resource: Record<string, unknown>;
  scope: string;
  origin: 'received' | 'local';
  received_at: number;
}

export interface IngestInfo {
  endpoint: string;
  protocol: string;
  /** Only ever filled for a caller on this machine — see `otel/auth.py`. */
  token: string | null;
  token_required_remote: boolean;
  last_received_at: number | null;
  received_traces: number;
  /** `host:port` when the opt-in gRPC receiver is listening; null when it is not. */
  grpc_endpoint: string | null;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/otel${path}`, init);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export const getIngestInfo = () => req<IngestInfo>('/ingest');

export const rotateIngestToken = () => req<IngestInfo>('/ingest/rotate', { method: 'POST' });

/**
 * The spans behind a run, from whichever side it came from.
 *
 * A received run carries its trace id in `meta.otel`; a built-in turn's trace id is
 * *derived* from its `turn_id` on the server (`otel/ids.py`), so the browser asks by
 * turn and never re-implements the hash. Null when the run has neither — an import,
 * or a run recorded before the node traced.
 */
export function getRunSpans(run: TrajectoryRun): Promise<OtelSpan[]> | null {
  const otel = run.meta?.otel as { trace_id?: unknown } | undefined;
  if (otel && typeof otel.trace_id === 'string' && otel.trace_id) {
    return req<OtelSpan[]>(`/traces/${encodeURIComponent(otel.trace_id)}`);
  }
  if (run.turn_id && run.source !== 'peer' && run.source !== 'imported') {
    return req<OtelSpan[]>(`/turn/${encodeURIComponent(run.turn_id)}`);
  }
  return null;
}
