/**
 * Span-tree geometry for the Trace view. Pure, so the layout is testable headless.
 *
 * Unlike `timeline-model.ts` this **does** model concurrency and nesting: a span
 * tree is exactly the shape a flat round-by-round waterfall cannot draw — a
 * delegate's whole sub-turn inside one tool call, a LangGraph node fanning out, an
 * MCP server's own spans under the call that reached it.
 *
 * Rows are the tree in depth-first order, children by start time, so a row's
 * indentation *is* its parentage. Geometry is fractions of the trace's span, so
 * the view reflows with the pane and needs no measured width.
 */
import type { OtelSpan } from '../otel-api';

export type SpanClass = 'agent' | 'llm' | 'tool' | 'other';

export interface TraceRow {
  span: OtelSpan;
  depth: number;
  /** 0..1 from the earliest start. */
  left: number;
  /** 0..1, floored so an instant is still a visible tick. */
  width: number;
  durationMs: number | null;
  cls: SpanClass;
  failed: boolean;
  /** Its parent is not in this trace — a partial trace, or the caller's own span. */
  orphan: boolean;
}

export interface TraceModel {
  rows: TraceRow[];
  spanMs: number;
  startNs: number;
}

const MIN_WIDTH = 0.004;

/** Mirrors `backend/modules/otel/mapping.classify`, reduced to what colours a bar. */
export function classify(span: OtelSpan): SpanClass {
  const a = span.attrs;
  const op = a['gen_ai.operation.name'];
  if (op === 'invoke_agent' || op === 'create_agent') return 'agent';
  if (op === 'chat' || op === 'text_completion' || op === 'generate_content') return 'llm';
  if (op === 'execute_tool') return 'tool';
  const oi = typeof a['openinference.span.kind'] === 'string' ? a['openinference.span.kind'] : '';
  if (oi) {
    const kind = String(oi).toUpperCase();
    if (kind === 'AGENT') return 'agent';
    if (kind === 'LLM') return 'llm';
    if (kind === 'TOOL') return 'tool';
    return 'other';
  }
  if ('gen_ai.tool.name' in a) return 'tool';
  if ('gen_ai.request.model' in a || 'llm.model_name' in a) return 'llm';
  return 'other';
}

export function buildTrace(spans: OtelSpan[]): TraceModel {
  if (!spans.length) return { rows: [], spanMs: 0, startNs: 0 };
  const byId = new Map(spans.map((s) => [s.span_id, s]));
  const children = new Map<string, OtelSpan[]>();
  const roots: OtelSpan[] = [];
  for (const span of spans) {
    if (span.parent_span_id && byId.has(span.parent_span_id)) {
      const list = children.get(span.parent_span_id) ?? [];
      list.push(span);
      children.set(span.parent_span_id, list);
    } else {
      roots.push(span);
    }
  }
  const byStart = (a: OtelSpan, b: OtelSpan) =>
    a.start_ns - b.start_ns || a.end_ns - b.end_ns || a.span_id.localeCompare(b.span_id);

  const starts = spans.map((s) => s.start_ns).filter((n) => n > 0);
  const ends = spans.map((s) => Math.max(s.end_ns, s.start_ns)).filter((n) => n > 0);
  const startNs = starts.length ? Math.min(...starts) : 0;
  const endNs = ends.length ? Math.max(...ends) : startNs;
  const totalNs = Math.max(endNs - startNs, 1);

  const rows: TraceRow[] = [];
  // Iterative, with a visited set: a cycle in received parent ids (a buggy
  // exporter) must not hang the pane.
  const seen = new Set<string>();
  const stack: { span: OtelSpan; depth: number }[] = roots
    .sort(byStart)
    .reverse()
    .map((span) => ({ span, depth: 0 }));
  const leftovers = () =>
    // Spans only reachable through a cycle have no root; surface them as roots
    // rather than dropping them from the view without a word.
    spans.filter((s) => !seen.has(s.span_id)).sort(byStart);
  while (stack.length || rows.length < spans.length) {
    if (!stack.length) {
      const next = leftovers()[0];
      if (!next) break;
      stack.push({ span: next, depth: 0 });
    }
    const { span, depth } = stack.pop() as { span: OtelSpan; depth: number };
    if (seen.has(span.span_id)) continue;
    seen.add(span.span_id);
    const durationNs = span.end_ns > span.start_ns ? span.end_ns - span.start_ns : null;
    rows.push({
      span,
      depth,
      left: span.start_ns > 0 ? (span.start_ns - startNs) / totalNs : 0,
      width: Math.max((durationNs ?? 0) / totalNs, MIN_WIDTH),
      durationMs: durationNs == null ? null : durationNs / 1e6,
      cls: classify(span),
      failed: span.status_code === 2,
      orphan: Boolean(span.parent_span_id) && !byId.has(span.parent_span_id),
    });
    const kids = (children.get(span.span_id) ?? []).slice().sort(byStart).reverse();
    for (const kid of kids) stack.push({ span: kid, depth: depth + 1 });
  }
  return { rows, spanMs: totalNs / 1e6, startNs };
}
