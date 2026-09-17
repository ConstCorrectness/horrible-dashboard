/**
 * Wording on the MCP activity dashboard.
 *
 * These strings carry distinctions the numbers alone lose: a server whose calls were
 * never priced is not a free server, and a dollar figure over the runs that used a
 * server is not what the server cost.
 */
import { describe, expect, it } from 'vitest';

import type { McpServerActivity } from '../api';
import { pctText, runCost } from '../panels/ActivitySection';

function server(runs: McpServerActivity['runs']): McpServerActivity {
  return {
    server_id: 's',
    calls: 1,
    tool_errors: 0,
    transport_errors: 0,
    tool_error_rate: 0,
    transport_error_rate: 0,
    p50_ms: 1,
    p95_ms: 1,
    p99_ms: 1,
    request_bytes: 0,
    response_bytes: 0,
    content_blocks: 0,
    last_at: 0,
    tools: [],
    runs,
  };
}

describe('runCost', () => {
  it('names the question it answers and how many runs were priced', () => {
    expect(
      runCost(server({ runs_seen: 4, runs_priced: 3, cost_usd_of_runs_using_server: 1.234 })),
    ).toBe('runs that used it: $1.23 across 3 of 4 priced');
  });

  it('never presents unpriced runs as free', () => {
    const text = runCost(
      server({ runs_seen: 2, runs_priced: 0, cost_usd_of_runs_using_server: null }),
    );
    expect(text).toBe('2 agent runs, none priced');
    expect(text).not.toMatch(/\$0|free/);
  });

  it('says free only for a known zero', () => {
    expect(
      runCost(server({ runs_seen: 1, runs_priced: 1, cost_usd_of_runs_using_server: 0 })),
    ).toContain('free');
  });

  it('distinguishes calls from outside any agent run', () => {
    expect(
      runCost(server({ runs_seen: 0, runs_priced: 0, cost_usd_of_runs_using_server: null })),
    ).toBe('no recorded agent runs used it');
  });
});

describe('pctText', () => {
  it('keeps "never ran" apart from "never failed"', () => {
    expect(pctText(null)).toBe('—');
    expect(pctText(0)).toBe('0%');
  });

  it('does not round a real failure down to zero', () => {
    expect(pctText(0.0004)).toBe('<0.1%');
    expect(pctText(0.125)).toBe('12.5%');
  });
});
