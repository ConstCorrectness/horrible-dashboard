/**
 * Literal provider requests: every call a turn made, as the provider received it.
 *
 * The round snapshots in `store.ts` are the context as the harness *assembled* it,
 * one labelled block per message the orchestrator built. These are the context as
 * the *wire* carried it — one joined system message, a nudge sent as `user`, tool
 * calls and their ids intact — with the block labels kept as `segments` inside each
 * sent message. Mirrors `backend/modules/interpretability/models.py`.
 *
 * Summaries arrive live on the `interpretability` channel (`request` events, once
 * when the call goes out and again when it returns); bodies are fetched on demand
 * and cached once the call has finished, since a finished request never changes.
 */
import { apiGet } from '../../api';
import { subscribeChannel } from '../../ws';

export interface RequestSegment {
  /** Index into the orchestrator's message list, before normalization. */
  src: number;
  /** Character range inside the sent message's `content`. */
  start: number;
  end: number;
  kind: string;
  label: string;
  toolName: string | null;
  /** Reconstructed requests only: clipped in the stored snapshot. */
  clipped: boolean;
}

export interface RequestMessage {
  index: number;
  role: string;
  /** Content address: equal across requests exactly when byte-identical. */
  hash: string;
  body: Record<string, unknown>;
  tokens: number;
  chars: number;
  segments: RequestSegment[];
  /** The role the orchestrator gave it when the wire changed it. */
  fromRole: string | null;
}

export interface RequestSummary {
  id: string;
  turnId: string;
  round: number;
  /** 0 for the round's first call, 1 for the forced-tool retry. */
  attempt: number;
  ts: number;
  dialect: string;
  provider: string;
  model: string;
  status: 'pending' | 'ok' | 'error';
  messageCount: number;
  /** Tokenizer estimate of messages + tool schemas; see `exact`. */
  estTokens: number;
  exact: boolean;
  /** What the provider reported. Null means it did not say — never zero. */
  promptTokens: number | null;
  completionTokens: number | null;
  toolCalls: number;
  toolNames: string[];
  responsePreview: string;
  durationMs: number | null;
  error: string | null;
  /** Rebuilt from a clipped snapshot for a turn that predates literal capture. */
  reconstructed: boolean;
}

export interface RequestDetail extends RequestSummary {
  messages: RequestMessage[];
  /** Tool schemas as sent. A reconstructed request has names only. */
  tools: Record<string, unknown>[];
  toolsHash: string;
  toolsTokens: number;
  /** Everything in the body other than `messages` and `tools`. */
  params: Record<string, unknown>;
  /** The assistant message the call returned, `reasoning_content` included. */
  response: Record<string, unknown> | null;
  contextLength: number | null;
  notes: string[];
}

// --- live summaries ------------------------------------------------------------

const byTurn = new Map<string, RequestSummary[]>();
const listeners = new Set<() => void>();
let started = false;

function emit(): void {
  for (const listener of listeners) listener();
}

/** A finished call never goes back to pending: a late `pending` frame (the socket
 *  and the fetch race) must not undo the `ok` that already arrived. */
function newer(a: RequestSummary, b: RequestSummary): RequestSummary {
  if (a.status !== 'pending' && b.status === 'pending') return a;
  return b;
}

function merge(incoming: RequestSummary[]): void {
  const touched = new Set<string>();
  for (const summary of incoming) {
    const list = byTurn.get(summary.turnId) ?? [];
    const at = list.findIndex((r) => r.id === summary.id);
    const next =
      at >= 0 ? list.map((r, i) => (i === at ? newer(r, summary) : r)) : [...list, summary];
    next.sort((a, b) => a.round - b.round || a.attempt - b.attempt || a.ts - b.ts);
    byTurn.set(summary.turnId, next);
    touched.add(summary.turnId);
  }
  if (touched.size) emit();
}

function start(): void {
  if (started) return;
  started = true;
  subscribeChannel('interpretability', (msg) => {
    if (msg.event !== 'request') return;
    const data = msg.data as RequestSummary | undefined;
    if (data?.id && data.turnId) merge([data]);
  });
}

export function subscribeRequests(listener: () => void): () => void {
  listeners.add(listener);
  start();
  return () => {
    listeners.delete(listener);
  };
}

/** A turn's calls in order. `undefined` until loaded or streamed. */
export function getTurnRequests(turnId: string): RequestSummary[] | undefined {
  return byTurn.get(turnId);
}

/** Back-fill a turn — on open, and for a turn the socket never told us about. */
export async function loadTurnRequests(turnId: string): Promise<void> {
  try {
    const res = await apiGet<{ requests: RequestSummary[] }>(
      `/interpretability/turns/${encodeURIComponent(turnId)}/requests`,
    );
    if (!res.requests.length && !byTurn.has(turnId)) {
      // Remember "none" so the view can say so rather than wait forever.
      byTurn.set(turnId, []);
      emit();
      return;
    }
    merge(res.requests);
  } catch {
    // Backend restarting: whatever the socket has delivered stays.
  }
}

// --- bodies --------------------------------------------------------------------

const details = new Map<string, RequestDetail>();

/** One call in full. Cached once finished; a pending call is re-read each time. */
export async function getRequestDetail(id: string): Promise<RequestDetail> {
  const cached = details.get(id);
  if (cached && cached.status !== 'pending') return cached;
  const detail = await apiGet<RequestDetail>(
    `/interpretability/requests/${encodeURIComponent(id)}`,
  );
  details.set(id, detail);
  return detail;
}
