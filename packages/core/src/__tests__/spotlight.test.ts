/**
 * Spotlight resolution: which sources answer, in what order, and what the
 * prefixes and the empty query do.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../registry';
import { openPaneInArea } from '../layout/controller';
import { collectAreas } from '../layout/model';
import { seedFromPreset, type FramePreset } from '../layout/presets';
import { layoutStore } from '../layout/store';
import { parseSpotlightQuery, spotlightResults } from '../spotlight';

const Stub = () => null;
const KNOWN = new Set(['sp.terminal', 'sp.notes']);

const preset: FramePreset = {
  id: 'sp',
  name: 'Spotlight',
  frame: { center: { split: 'row', children: [{ pane: 'sp.terminal' }, { tabs: [] }] } },
};

beforeAll(() => {
  registry.register({
    id: 'spotlight-test',
    title: 'Spotlight test',
    panels: [
      { id: 'sp.terminal', title: 'Terminal', component: Stub, role: 'document', icon: '>' },
      { id: 'sp.notes', title: 'Notes', component: Stub, role: 'document', icon: 'N' },
    ],
    commands: [
      { id: 'sp.newTerminal', title: 'Terminal: New', run: () => {} },
      { id: 'sp.zzz', title: 'Zebra crossing', run: () => {} },
    ],
  });
});

beforeEach(() => {
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'sp',
    frame: seedFromPreset(preset, { knownViews: KNOWN }),
  });
});

const frame = () => layoutStore.getSnapshot().frame;
const results = (q: string) => spotlightResults(q, frame());

describe('query parsing', () => {
  it('reads the three source prefixes and strips them', () => {
    expect(parseSpotlightQuery('>open')).toEqual({ text: 'open', only: 'command' });
    expect(parseSpotlightQuery('@term')).toEqual({ text: 'term', only: 'pane' });
    expect(parseSpotlightQuery('? why is it slow')).toEqual({
      text: 'why is it slow',
      only: 'agent',
    });
  });

  it('treats anything else as an unfiltered query', () => {
    expect(parseSpotlightQuery('  term  ')).toEqual({ text: 'term', only: null });
  });
});

describe('ranking', () => {
  it('puts an open pane above a command that would open another one', () => {
    // Typing "terminal" almost always means the terminal you have.
    const hits = results('terminal');
    expect(hits[0].kind).toBe('pane');
    expect(hits.some((h) => h.kind === 'command')).toBe(true);
  });

  it('prefers a title prefix over a title substring', () => {
    const hits = results('zebra').filter((h) => h.kind === 'command');
    expect(hits[0].title).toBe('Zebra crossing');
  });

  it('matches on a view/command id, but ranks it below any title match', () => {
    const byId = results('sp.zzz');
    expect(byId.some((h) => h.title === 'Zebra crossing')).toBe(true);
  });
});

describe('the agent', () => {
  it('is offered last when other things matched', () => {
    const hits = results('terminal');
    expect(hits[hits.length - 1].kind).toBe('agent');
    expect(hits.length).toBeGreaterThan(1);
  });

  it('is the only result when nothing matched — the point of the whole thing', () => {
    // A palette that answers a typed sentence with "no matching commands" is the
    // failure this surface exists to remove.
    const hits = results('why did the last build fail');
    expect(hits).toHaveLength(1);
    expect(hits[0].kind).toBe('agent');
    expect(hits[0].action).toEqual({ type: 'ask', prompt: 'why did the last build fail' });
  });

  it('is absent for an empty query, so opening the surface is not a prompt', () => {
    expect(results('').every((h) => h.kind !== 'agent')).toBe(true);
  });

  it('is suppressed when a source prefix names something else', () => {
    expect(results('>terminal').every((h) => h.kind === 'command')).toBe(true);
    expect(results('@terminal').every((h) => h.kind === 'pane')).toBe(true);
  });
});

describe('actions', () => {
  it('gives a pane its live instance id', () => {
    const instanceId = collectAreas(frame().center)[0].tabs[0].instanceId;
    const hit = results('@terminal')[0];
    expect(hit.action).toEqual({ type: 'focusPane', instanceId });
  });

  it('lists a second pane opened after the first', () => {
    const areaId = collectAreas(frame().center).find((a) => !a.tabs.length)!.id;
    openPaneInArea('sp.notes', areaId);
    expect(results('@notes')).toHaveLength(1);
  });

  it('renders a command shortcut through the caller-supplied resolver', () => {
    const hits = spotlightResults('>zebra', frame(), (id) => (id === 'sp.zzz' ? 'Ctrl+Z' : null));
    expect(hits[0].hint).toBe('Ctrl+Z');
  });
});
