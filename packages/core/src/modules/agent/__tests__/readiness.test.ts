/**
 * The readiness branch.
 *
 * This used to be a single expression — `configured && reachable` — rendered as
 * one grey placeholder reading "Agent not ready". Four genuinely different
 * situations (still asking, backend gone, no model chosen, model server down)
 * arrived at the user as the same four words, and the two that a user can
 * actually fix need opposite actions.
 *
 * The failure mode is silent by construction: collapsing the states does not
 * error, it just tells everyone the least useful true thing. So the mapping gets
 * a test rather than trusting a boolean chain to stay readable.
 */
import { describe, expect, it, vi } from 'vitest';

import { planFor } from '../AgentReadiness';
import type { AgentStatus } from '../api';

const noop = () => {};

function status(over: Partial<AgentStatus>): AgentStatus {
  return {
    configured: true,
    provider: 'ollama',
    model: 'gemma4:e2b',
    endpoint: 'http://127.0.0.1:11434',
    reachable: true,
    available_models: [],
    ...over,
  } as AgentStatus;
}

describe('planFor', () => {
  it('says nothing when the agent is actually ready', () => {
    expect(planFor(status({}), noop)).toBeNull();
  });

  it('distinguishes "no model chosen" from "server not answering"', () => {
    const unconfigured = planFor(status({ configured: false }), noop);
    const unreachable = planFor(status({ reachable: false }), noop);

    expect(unconfigured?.title).toBe('No model selected');
    expect(unreachable?.title).toContain('ollama');
    // The whole point: two different problems must not produce one message.
    expect(unconfigured?.title).not.toBe(unreachable?.title);
  });

  it('shows the endpoint it tried, but only when there was one', () => {
    expect(planFor(status({ reachable: false }), noop)?.endpoint).toBe('http://127.0.0.1:11434');
    // Nothing was tried yet if no model is configured, so claiming an endpoint
    // failed would be inventing a cause.
    expect(planFor(status({ configured: false }), noop)?.endpoint).toBeUndefined();
  });

  it('treats a dead backend as its own state, not as "not configured"', () => {
    const plan = planFor('backend-down', noop);
    expect(plan?.kind).toBe('fail');
    expect(plan?.title).toBe('Backend not answering');
  });

  it('reports the unresolved state rather than guessing a verdict', () => {
    // "Could not ask yet" is a real state and must not render as a failure —
    // this is the product's own three-state rule applied to a status fetch.
    const plan = planFor('loading', noop);
    expect(plan?.kind).toBe('info');
    expect(plan?.actions).toHaveLength(0);
  });

  it('gives every actionable state a control that runs', () => {
    const retry = vi.fn();
    for (const s of [status({ reachable: false }), 'backend-down' as const]) {
      const plan = planFor(s, retry);
      expect(plan?.actions.length).toBeGreaterThan(0);
      plan?.actions[0].run();
    }
    expect(retry).toHaveBeenCalledTimes(2);
  });
});
