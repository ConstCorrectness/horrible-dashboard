import { describe, expect, it } from 'vitest';

import type { Harness, TrajectoryRun } from '../api';
import {
  buildLineage,
  diffHarness,
  fitTransform,
  neighbourhood,
  placeLineage,
  verdictOf,
  yAxis,
} from '../panels/lineage-model';

function harness(fingerprint: string, over: Partial<Harness> = {}): Harness {
  return {
    fingerprint,
    agent_id: 'main',
    model: 'org/model-a',
    provider: 'nim',
    system_prompt: 'be helpful',
    tool_names: ['read', 'write'],
    params: { temperature: 0.7 },
    label: `main @ ${fingerprint}`,
    first_seen: 0,
    last_seen: 0,
    run_count: 0,
    ...over,
  };
}

let clock = 1000;
function run(id: string, over: Partial<TrajectoryRun> = {}): TrajectoryRun {
  clock += 10;
  return {
    id,
    dataset_id: 'ds',
    source: 'local',
    external_id: null,
    turn_id: null,
    parent_run_id: null,
    harness: 'h1',
    agent_id: 'main',
    agent_name: '',
    model: 'org/model-a',
    provider: 'nim',
    goal: id,
    status: 'complete',
    outcome: null,
    reward: null,
    steps: 3,
    rounds: 2,
    tokens_in: null,
    tokens_out: null,
    started_at: clock,
    finished_at: null,
    duration_ms: 100,
    cost_usd: null,
    node_id: '',
    person_id: '',
    error: '',
    meta: {},
    ...over,
  };
}

describe('diffHarness', () => {
  it('names the most significant change as the edge, and lists every change', () => {
    const { edge, changes } = diffHarness(
      harness('a'),
      harness('b', {
        system_prompt: 'be helpful and brief',
        tool_names: ['read', 'search'],
        params: { temperature: 0.2 },
      }),
    );
    expect(edge).toBe('prompt');
    expect(changes).toEqual(['prompt +10 chars', 'tools +1 −1 (2)', 'temperature 0.7 → 0.2']);
  });

  it('ranks a model swap above everything else', () => {
    const { edge, changes } = diffHarness(
      harness('a'),
      harness('b', { model: 'org/model-b', system_prompt: 'x' }),
    );
    expect(edge).toBe('model');
    expect(changes[0]).toBe('model model-a → model-b');
  });

  it('calls a fingerprint change with no visible difference a schema change', () => {
    expect(diffHarness(harness('a'), harness('b'))).toEqual({ edge: 'schema', changes: [] });
  });
});

describe('verdictOf', () => {
  it('does not treat a finished, ungraded run as a success', () => {
    expect(verdictOf(run('r'))).toBe('ungraded');
    expect(verdictOf(run('r', { outcome: 'success' }))).toBe('success');
    expect(verdictOf(run('r', { status: 'failed' }))).toBe('failure');
    expect(verdictOf(run('r', { status: 'running' }))).toBe('running');
  });
});

describe('buildLineage', () => {
  const harnesses = [
    harness('h1'),
    harness('h2', { system_prompt: 'new prompt' }),
    harness('i1', { agent_id: 'intake', model: 'org/small' }),
  ];
  const runs = [
    run('r1'),
    run('r2', { harness: 'h2' }),
    run('r3'),
    run('q1', { agent_id: 'intake', harness: 'i1', model: 'org/small' }),
    run('x1', { agent_id: '', harness: null, parent_run_id: 'r1' }),
  ];

  it('chains each agent’s harnesses in the order they first ran', () => {
    const lin = buildLineage(runs, harnesses, 'harnesses');
    const parent = Object.fromEntries(lin.rows.map((r) => [r.key, r.pkey]));
    expect(parent['agent:main']).toBe('__root');
    expect(parent['harness:h1']).toBe('agent:main');
    expect(parent['harness:h2']).toBe('harness:h1');
    expect(lin.rows.find((r) => r.key === 'harness:h2')!.edge).toBe('prompt');
    // A harness keeps every run that used it, even when another revision ran between.
    expect(lin.rows.find((r) => r.key === 'harness:h1')!.runs.map((r) => r.id)).toEqual([
      'r1',
      'r3',
    ]);
  });

  it('folds runs into their harness at the harness level and adds them as leaves at the run level', () => {
    const folded = buildLineage(runs, harnesses, 'harnesses');
    expect(folded.rows.some((r) => r.kind === 'run')).toBe(false);
    expect(folded.memberOf.r3).toBe('harness:h1');

    const leaves = buildLineage(runs, harnesses, 'runs');
    expect(leaves.memberOf.r3).toBe('run:r3');
    expect(leaves.rows.find((r) => r.key === 'run:r3')!.pkey).toBe('harness:h1');
  });

  it('keeps runs with no agent or harness, under a synthetic node', () => {
    const lin = buildLineage(runs, harnesses, 'runs');
    expect(lin.rows.find((r) => r.key === 'harness:none:external')!.pkey).toBe('agent:external');
  });

  it('records delegation only when both runs are present', () => {
    expect(buildLineage(runs, harnesses, 'runs').delegations).toEqual([
      { parent: 'r1', child: 'x1' },
    ]);
    expect(buildLineage(runs.slice(1), harnesses, 'runs').delegations).toEqual([]);
  });

  it('lights ancestors, descendants and the far end of a delegation', () => {
    const lin = buildLineage(runs, harnesses, 'runs');
    const lit = neighbourhood(lin, 'run:r1');
    expect(lit.has('harness:h1')).toBe(true);
    expect(lit.has('agent:main')).toBe(true);
    expect(lit.has('run:x1')).toBe(true);
    expect(lit.has('agent:intake')).toBe(false);
    // Focusing a harness lights the revisions after it, not its siblings' runs.
    const fromH1 = neighbourhood(lin, 'harness:h1');
    expect(fromH1.has('harness:h2')).toBe(true);
    expect(fromH1.has('run:q1')).toBe(false);
  });
});

describe('placeLineage', () => {
  it('lays generations along x and drops the hidden root', () => {
    const lin = buildLineage(
      [run('r1'), run('r2', { harness: 'h2' })],
      [harness('h1'), harness('h2', { model: 'org/b' })],
      'runs',
    );
    const placed = placeLineage(lin, { rowGap: 10, colGap: 100 });
    expect(placed.nodes.some((n) => n.row.kind === 'root')).toBe(false);
    expect(placed.byKey.get('agent:main')!.x).toBe(100);
    expect(placed.byKey.get('harness:h1')!.x).toBe(200);
    expect(placed.byKey.get('harness:h2')!.x).toBe(300);
    expect(placed.links.find((l) => l.target.row.key === 'harness:h2')!.edge).toBe('model');
  });

  it('pulls rows apart toward the stage aspect, capped, without moving columns', () => {
    const lin = buildLineage(
      [run('r1'), run('r2'), run('r3', { harness: 'h2' })],
      [harness('h1'), harness('h2', { model: 'org/b' })],
      'runs',
    );
    const flat = placeLineage(lin, { rowGap: 10, colGap: 100 });
    const height = (p: typeof flat) => p.bounds.y1 - p.bounds.y0;
    const width = flat.bounds.x1 - flat.bounds.x0;

    const stretched = placeLineage(lin, { rowGap: 10, colGap: 100, aspect: 2 });
    expect(stretched.bounds.x1 - stretched.bounds.x0).toBe(width);
    expect(height(stretched)).toBeCloseTo(Math.min(4 * height(flat), width / 2));

    // A stage wider than the tree never squashes it.
    const wide = placeLineage(lin, { rowGap: 10, colGap: 100, aspect: 1000 });
    expect(height(wide)).toBe(height(flat));
  });
});

describe('fitTransform', () => {
  it('centres the bounds inside the padded stage', () => {
    const t = fitTransform({ x0: 0, x1: 100, y0: 0, y1: 50 }, 300, 200, { pad: 50, maxScale: 10 });
    expect(t.k).toBe(2);
    expect(t.x).toBe(50);
    expect(t.y).toBe(50);
  });
});

describe('yAxis', () => {
  it('snaps a log axis to whole decades', () => {
    const axis = yAxis([57, 263_000], true, 100, 0);
    expect(axis.ticks).toEqual([10, 100, 1000, 10_000, 100_000, 1_000_000]);
    expect(axis.map(10)).toBe(100);
    expect(axis.map(1_000_000)).toBe(0);
  });

  it('starts a linear axis at zero on a 1/2/5 step', () => {
    const axis = yAxis([1, 12], false, 100, 0);
    expect(axis.ticks).toEqual([0, 5, 10, 15]);
    expect(axis.map(0)).toBe(100);
  });
});
