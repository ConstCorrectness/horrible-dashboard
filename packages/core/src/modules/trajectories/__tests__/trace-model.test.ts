/**
 * Span-tree geometry. Like the Timeline, a wrong tree is invisible — indentation
 * that looks plausible whatever the parentage was — so the rules are pinned here.
 */
import { describe, expect, it } from 'vitest';

import type { OtelSpan } from '../otel-api';
import { buildTrace, classify } from '../panels/trace-model';

const T0 = 1_700_000_000_000_000_000;
const MS = 1_000_000;

function span(
  id: string,
  parent: string,
  start: number,
  end: number,
  attrs: Record<string, unknown> = {},
): OtelSpan {
  return {
    trace_id: 't'.repeat(32),
    span_id: id,
    parent_span_id: parent,
    name: id,
    kind: 1,
    start_ns: T0 + start * MS,
    end_ns: T0 + end * MS,
    status_code: 0,
    status_message: '',
    attrs,
    events: [],
    resource: {},
    scope: '',
    origin: 'received',
    received_at: 0,
  };
}

describe('buildTrace', () => {
  it('orders depth-first with children by start, and indents by parentage', () => {
    const model = buildTrace([
      span('tool2', 'agent', 60, 90),
      span('agent', '', 0, 100),
      span('chat', 'agent', 0, 50),
      span('nested', 'tool2', 70, 80),
    ]);
    expect(model.rows.map((r) => [r.span.span_id, r.depth])).toEqual([
      ['agent', 0],
      ['chat', 1],
      ['tool2', 1],
      ['nested', 2],
    ]);
    expect(model.spanMs).toBeCloseTo(100);
  });

  it('places bars as fractions of the whole trace, concurrency included', () => {
    const model = buildTrace([
      span('root', '', 0, 100),
      span('a', 'root', 10, 60),
      span('b', 'root', 20, 70),
    ]);
    const a = model.rows.find((r) => r.span.span_id === 'a');
    const b = model.rows.find((r) => r.span.span_id === 'b');
    expect(a?.left).toBeCloseTo(0.1);
    expect(a?.width).toBeCloseTo(0.5);
    // Overlapping siblings keep their own geometry rather than being serialized.
    expect(b?.left).toBeCloseTo(0.2);
  });

  it('marks spans whose parent is not in the trace as orphans, at depth 0', () => {
    const model = buildTrace([span('late', 'missing-root', 0, 10)]);
    expect(model.rows[0]).toMatchObject({ depth: 0, orphan: true });
  });

  it('survives a parent cycle without hanging or dropping spans', () => {
    const model = buildTrace([span('x', 'y', 0, 10), span('y', 'x', 5, 10)]);
    expect(model.rows.map((r) => r.span.span_id).sort()).toEqual(['x', 'y']);
  });

  it('gives an instant a visible, non-zero width', () => {
    const model = buildTrace([span('root', '', 0, 100), span('tick', 'root', 50, 50)]);
    const tick = model.rows.find((r) => r.span.span_id === 'tick');
    expect(tick?.durationMs).toBeNull();
    expect(tick?.width).toBeGreaterThan(0);
  });
});

describe('classify', () => {
  it('reads both GenAI semconv and OpenInference', () => {
    expect(classify(span('a', '', 0, 1, { 'gen_ai.operation.name': 'chat' }))).toBe('llm');
    expect(classify(span('a', '', 0, 1, { 'openinference.span.kind': 'TOOL' }))).toBe('tool');
    expect(classify(span('a', '', 0, 1, { 'openinference.span.kind': 'RETRIEVER' }))).toBe('other');
    expect(classify(span('a', '', 0, 1, { 'gen_ai.request.model': 'm' }))).toBe('llm');
  });
});
