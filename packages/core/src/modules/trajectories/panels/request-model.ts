/**
 * The context inspector's data model: pure functions over one literal provider
 * request (see `interpretability/requests.ts`), kept free of React so they run in
 * core's DOM-less vitest.
 *
 * The vocabulary is the wire's: a message has a `role`, and only the four roles the
 * provider knows. Where a message came from — the skill catalog, a tool guide, a
 * force-tool nudge — is *provenance*, carried as segments and drawn as a tint, never
 * as a role the model did not see.
 */
import type {
  RequestDetail,
  RequestMessage,
  RequestSegment,
  RequestSummary,
} from '../../interpretability/requests';
import { FAMILY_LABEL, familyOf, type SourceFamily } from './follow-model';

/** `R2`, `R2 · retry`. A second call in one round is the forced-tool retry. */
export function requestLabel(r: { round: number; attempt: number }): string {
  if (r.attempt === 0) return `R${r.round}`;
  return r.attempt === 1 ? `R${r.round} · retry` : `R${r.round} · retry ${r.attempt}`;
}

const ROLE_FAMILY: Record<string, SourceFamily> = {
  system: 'system',
  user: 'user',
  assistant: 'assistant',
  tool: 'result',
};

/** The hue a message is drawn in: its provenance when it has one, else its role. */
export function messageFamily(m: RequestMessage): SourceFamily {
  if (m.segments.length) return familyOf(m.segments[0].kind);
  return ROLE_FAMILY[m.role] ?? 'history';
}

/** A message's content as text. Structured content (multimodal parts) is shown as
 *  the JSON it is. */
export function contentText(body: Record<string, unknown>): string {
  const content = body.content;
  if (typeof content === 'string') return content;
  if (content == null) return '';
  return JSON.stringify(content, null, 2);
}

// --- provenance tint -----------------------------------------------------------

export interface Piece {
  text: string;
  /** Null for the seams between parts (the blank line the join inserts). */
  family: SourceFamily | null;
  label: string | null;
  clipped: boolean;
}

/**
 * Split a message's content into its provenance pieces, for tinting. Only a message
 * the wire joined from several parts has more than one; anything else is one piece.
 */
export function contentPieces(text: string, segments: RequestSegment[]): Piece[] {
  if (segments.length <= 1) {
    const s = segments[0];
    return [
      {
        text,
        family: s ? familyOf(s.kind) : null,
        label: s?.label ?? null,
        clipped: s?.clipped ?? false,
      },
    ];
  }
  const pieces: Piece[] = [];
  let at = 0;
  for (const s of [...segments].sort((a, b) => a.start - b.start)) {
    const start = Math.max(at, Math.min(s.start, text.length));
    const end = Math.max(start, Math.min(s.end, text.length));
    if (start > at)
      pieces.push({ text: text.slice(at, start), family: null, label: null, clipped: false });
    if (end > start)
      pieces.push({
        text: text.slice(start, end),
        family: familyOf(s.kind),
        label: s.label,
        clipped: s.clipped,
      });
    at = end;
  }
  if (at < text.length)
    pieces.push({ text: text.slice(at), family: null, label: null, clipped: false });
  return pieces;
}

// --- call ↔ result pairing -----------------------------------------------------

export interface CallRef {
  /** Index of the assistant message that made the call. */
  message: number;
  /** Position within that message's `tool_calls`. */
  call: number;
  id: string;
  name: string;
}

export interface Pairing {
  /** Tool message index → the call it answers. */
  callFor: Map<number, CallRef>;
  /** Assistant message index → the tool messages that answered it, in order. */
  resultsFor: Map<number, number[]>;
}

function callName(call: Record<string, unknown>): string {
  const fn = call.function as Record<string, unknown> | undefined;
  if (fn && typeof fn.name === 'string') return fn.name;
  return typeof call.name === 'string' ? call.name : '';
}

export function toolCallsOf(body: Record<string, unknown> | null | undefined): CallRef[] {
  const calls = body?.tool_calls;
  if (!Array.isArray(calls)) return [];
  return calls.map((c, k) => {
    const call = (c ?? {}) as Record<string, unknown>;
    return {
      message: -1,
      call: k,
      id: typeof call.id === 'string' ? call.id : '',
      name: callName(call),
    };
  });
}

/**
 * Pair each tool result with the call it answers. The OpenAI dialect says so
 * outright (`tool_call_id`); Ollama does not send ids at all and keys results by
 * `tool_name`, so there the pairing is by name within the open calls, then order —
 * the same rule `agentpedia/rebuild.py` uses on the backend.
 */
export function pairToolCalls(messages: RequestMessage[]): Pairing {
  const callFor = new Map<number, CallRef>();
  const resultsFor = new Map<number, number[]>();
  const byId = new Map<string, CallRef>();
  let open: CallRef[] = [];

  const answer = (toolIdx: number, ref: CallRef) => {
    callFor.set(toolIdx, ref);
    resultsFor.set(ref.message, [...(resultsFor.get(ref.message) ?? []), toolIdx]);
    open = open.filter((c) => c !== ref);
    if (ref.id) byId.delete(ref.id);
  };

  messages.forEach((m, i) => {
    if (m.role === 'assistant') {
      const calls = toolCallsOf(m.body).map((c) => ({ ...c, message: i }));
      if (calls.length) open = calls;
      for (const c of calls) if (c.id) byId.set(c.id, c);
      return;
    }
    if (m.role !== 'tool') return;
    const id = typeof m.body.tool_call_id === 'string' ? m.body.tool_call_id : '';
    const byIdRef = id ? byId.get(id) : undefined;
    if (byIdRef) return answer(i, byIdRef);
    const name =
      typeof m.body.tool_name === 'string'
        ? m.body.tool_name
        : typeof m.body.name === 'string'
          ? m.body.name
          : '';
    const ref = (name && open.find((c) => c.name === name)) || open[0];
    if (ref) answer(i, ref);
  });
  return { callFor, resultsFor };
}

/** The messages to light up when `index` is hovered: its call or its results. */
export function partnersOf(index: number, pairing: Pairing): number[] {
  const call = pairing.callFor.get(index);
  if (call) return [call.message];
  return pairing.resultsFor.get(index) ?? [];
}

// --- new vs carried --------------------------------------------------------------

/**
 * Indexes of the messages that were not in the previous request. Multiset-aware:
 * two identical tool results are two messages, and only the extra one is new.
 * `null` when there is no previous request to compare against.
 */
export function newIndexes(
  prev: RequestMessage[] | undefined,
  cur: RequestMessage[],
): Set<number> | null {
  if (!prev) return null;
  const left = new Map<string, number>();
  for (const m of prev) left.set(m.hash, (left.get(m.hash) ?? 0) + 1);
  const out = new Set<number>();
  cur.forEach((m, i) => {
    const n = left.get(m.hash) ?? 0;
    if (n > 0) left.set(m.hash, n - 1);
    else out.add(i);
  });
  return out;
}

// --- minimap ---------------------------------------------------------------------

export interface MinimapItem {
  key: string;
  /** Message index; null for the tool-schema stripe and the free window. */
  index: number | null;
  family: SourceFamily | 'free';
  /** Flex weight: the item's share of the window, in tokens. */
  grow: number;
  tokens: number;
  label: string;
  isNew: boolean;
}

export interface Minimap {
  items: MinimapItem[];
  /** Estimated tokens in the request: messages + tool schemas. */
  used: number;
  /** The model's window when known. */
  window: number | null;
}

/**
 * The window drawn top to bottom in send order: the `tools[]` stripe first (it is a
 * separate field of the request, not a message), then each message, then — when the
 * model's window is known — the room left. Heights are token-proportional; the
 * renderer gives every item a minimum so a 3-token message stays hoverable.
 */
export function buildMinimap(detail: RequestDetail, fresh: Set<number> | null): Minimap {
  const items: MinimapItem[] = [];
  if (detail.toolsTokens > 0 || detail.tools.length) {
    items.push({
      key: 'tools',
      index: null,
      family: 'tools',
      grow: Math.max(1, detail.toolsTokens),
      tokens: detail.toolsTokens,
      label: `tools[] · ${detail.tools.length}`,
      isNew: false,
    });
  }
  for (const m of detail.messages) {
    items.push({
      key: `m${m.index}`,
      index: m.index,
      family: messageFamily(m),
      grow: Math.max(1, m.tokens),
      tokens: m.tokens,
      label: `#${m.index} ${m.role}`,
      isNew: fresh?.has(m.index) ?? false,
    });
  }
  const used = detail.messages.reduce((n, m) => n + m.tokens, 0) + detail.toolsTokens;
  const window = detail.contextLength && detail.contextLength > 0 ? detail.contextLength : null;
  if (window && window > used) {
    items.push({
      key: 'free',
      index: null,
      family: 'free',
      grow: window - used,
      tokens: window - used,
      label: 'unused window',
      isNew: false,
    });
  }
  return { items, used, window };
}

// --- the hover popover -----------------------------------------------------------

export interface PopoverModel {
  /** `#3 system` */
  title: string;
  /** "was system" when the wire changed the role. */
  roleNote: string | null;
  provenance: { label: string; family: SourceFamily; chars: number; toolName: string | null }[];
  tokens: number;
  exact: boolean;
  /** Null for the first request of a turn: nothing to compare against. */
  status: 'new' | 'carried' | null;
  partner: string | null;
  preview: string[];
  /** Lines not shown in the preview. */
  more: number;
}

const PREVIEW_LINES = 12;
const PREVIEW_WIDTH = 160;

export function popoverFor(
  detail: RequestDetail,
  index: number,
  pairing: Pairing,
  fresh: Set<number> | null,
): PopoverModel | null {
  const m = detail.messages[index];
  if (!m) return null;

  let partner: string | null = null;
  const call = pairing.callFor.get(index);
  if (call) partner = `answers #${call.message} · ${call.name || 'call'}`;
  const results = pairing.resultsFor.get(index);
  if (results?.length) partner = `answered by ${results.map((r) => `#${r}`).join(', ')}`;
  else if (m.role === 'assistant' && toolCallsOf(m.body).length)
    partner = `${toolCallsOf(m.body).length} call(s), no result in this request`;

  let text = contentText(m.body);
  if (!text.trim() && Array.isArray(m.body.tool_calls))
    text = JSON.stringify(m.body.tool_calls, null, 2);
  const lines = text.split('\n');

  return {
    title: `#${m.index} ${m.role}`,
    roleNote: m.fromRole ? `assembled as ${m.fromRole}, sent as ${m.role}` : null,
    provenance: m.segments.map((s) => ({
      label: s.label,
      family: familyOf(s.kind),
      chars: s.end - s.start,
      toolName: s.toolName,
    })),
    tokens: m.tokens,
    exact: detail.exact,
    status: fresh ? (fresh.has(index) ? 'new' : 'carried') : null,
    partner,
    preview: lines
      .slice(0, PREVIEW_LINES)
      .map((l) => (l.length > PREVIEW_WIDTH ? `${l.slice(0, PREVIEW_WIDTH - 1)}…` : l)),
    more: Math.max(0, lines.length - PREVIEW_LINES),
  };
}

// --- deep links --------------------------------------------------------------------

/**
 * Which call a deep link means. An id wins outright. A trajectory step knows its
 * round and the tool it ran: the call whose *response* asked for that tool (the
 * last such, since a retry supersedes). A wire event knows its round and when it
 * happened: the call in that round that started closest before it. Failing all of
 * that, the round's last call.
 */
export function resolveFocus(
  calls: RequestSummary[],
  focus: {
    requestId?: string | null;
    round?: number | null;
    toolName?: string | null;
    ts?: number | null;
  },
): string | null {
  if (focus.requestId && calls.some((c) => c.id === focus.requestId)) return focus.requestId;
  if (focus.round == null) return null;
  const inRound = calls.filter((c) => c.round === focus.round);
  if (!inRound.length) return null;
  if (focus.toolName) {
    const asked = inRound.filter((c) => c.toolNames.includes(focus.toolName as string));
    if (asked.length) return asked[asked.length - 1].id;
  }
  if (focus.ts != null) {
    const ts = focus.ts;
    const before = inRound.filter((c) => c.ts <= ts + 0.5);
    const pick = (before.length ? before : inRound).reduce((best, c) =>
      Math.abs(c.ts - ts) < Math.abs(best.ts - ts) ? c : best,
    );
    return pick.id;
  }
  return inRound[inRound.length - 1].id;
}

// --- the response side -----------------------------------------------------------

/** The tool messages in `next` that answer the calls `response` made. */
export function answersIn(
  response: Record<string, unknown> | null,
  next: RequestDetail | null,
): RequestMessage[] {
  if (!response || !next) return [];
  const ids = toolCallsOf(response).map((c) => c.id);
  if (!ids.length) return [];
  const pairing = pairToolCalls(next.messages);
  // The assistant message in `next` that is this response: the last one whose
  // call ids match (ids are unique within a turn), else simply the last one.
  const assistants = next.messages.filter((m) => m.role === 'assistant');
  const same =
    [...assistants].reverse().find((m) => {
      const theirs = toolCallsOf(m.body).map((c) => c.id);
      return theirs.length === ids.length && theirs.every((id, k) => id === ids[k]);
    }) ?? assistants[assistants.length - 1];
  if (!same) return [];
  return (pairing.resultsFor.get(same.index) ?? []).map((i) => next.messages[i]);
}

export function reasoningOf(response: Record<string, unknown> | null): string {
  const r = response?.reasoning_content ?? response?.reasoning;
  return typeof r === 'string' ? r : '';
}

export function familyLabel(family: SourceFamily | 'free'): string {
  return family === 'free' ? 'Free' : FAMILY_LABEL[family];
}

/** Tokens the provider counted beyond our estimate: the chat template's own
 *  wrapping (role headers, special tokens), plus whatever the estimate missed.
 *  Null when the provider did not report. */
export function templateGap(est: number, reported: number | null): number | null {
  return reported == null ? null : reported - est;
}
