/**
 * Minibuffer command matching and submit. Commands are registered synthetically
 * — a real module manifest reaches the editor's module-scope WebSocket and dies
 * without jsdom.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { matchCommands, minibuffer, resolveCommand } from '../minibuffer';
import { registry } from '../registry';

const ran: string[] = [];

beforeAll(() => {
  registry.register({
    id: 'mb-test',
    title: 'Minibuffer test',
    commands: [
      { id: 'mb.save', title: 'Test: Save', run: () => void ran.push('mb.save'), slash: 'save' },
      {
        id: 'mb.saveAs',
        title: 'Test: Save as…',
        run: () => void ran.push('mb.saveAs'),
        slash: 'save-as',
      },
      {
        id: 'mb.find',
        title: 'Test: Find symbol',
        run: () => void ran.push('mb.find'),
        slash: 'find',
      },
      // No slash: reachable by fuzzy title/id search only.
      { id: 'mb.visualize', title: 'Test: Open in visualizer', run: () => void ran.push('mb.viz') },
      {
        id: 'mb.boom',
        title: 'Test: Throws',
        run: () => {
          throw new Error('nope');
        },
      },
    ],
  });
});

beforeEach(() => {
  ran.length = 0;
  minibuffer.close();
  minibuffer.clearEcho();
});

describe('matchCommands', () => {
  it('ranks an exact slash hit first', () => {
    expect(resolveCommand('/save')?.id).toBe('mb.save');
    // ...even though `save-as` also starts with "save".
    expect(matchCommands('/save')[0].id).toBe('mb.save');
  });

  it('treats the leading slash as optional', () => {
    expect(resolveCommand('save')?.id).toBe('mb.save');
    expect(resolveCommand('/save')?.id).toBe(resolveCommand('save')?.id);
  });

  it('resolves a distinct slash name exactly', () => {
    expect(resolveCommand('/save-as')?.id).toBe('mb.saveAs');
  });

  it('finds a command with no slash name by title', () => {
    expect(resolveCommand('visualizer')?.id).toBe('mb.visualize');
  });

  it('returns nothing for a query that matches no command', () => {
    expect(matchCommands('zzzznope')).toEqual([]);
    expect(resolveCommand('zzzznope')).toBeNull();
  });

  it('respects the result limit', () => {
    expect(matchCommands('', 2)).toHaveLength(2);
  });
});

describe('minibuffer state', () => {
  it('opens seeded, and closing clears the query', () => {
    minibuffer.open('/sa');
    expect(minibuffer.getSnapshot()).toMatchObject({ open: true, query: '/sa' });
    minibuffer.close();
    expect(minibuffer.getSnapshot()).toMatchObject({ open: false, query: '' });
  });

  it('notifies subscribers on change', () => {
    let calls = 0;
    const stop = minibuffer.subscribe(() => calls++);
    minibuffer.open();
    minibuffer.setQuery('/save');
    stop();
    expect(calls).toBe(2);
  });
});

describe('minibuffer submit', () => {
  it('runs the resolved command and closes', async () => {
    minibuffer.open('/save');
    await expect(minibuffer.submit()).resolves.toBe(true);
    expect(ran).toEqual(['mb.save']);
    expect(minibuffer.getSnapshot()).toMatchObject({ open: false, query: '', echo: null });
  });

  it('reports an unmatched query in the echo area instead of failing silently', async () => {
    minibuffer.open('/zzzznope');
    await expect(minibuffer.submit()).resolves.toBe(false);
    expect(ran).toEqual([]);
    const { echo } = minibuffer.getSnapshot();
    expect(echo?.tone).toBe('error');
    expect(echo?.text).toContain('zzzznope');
  });

  it('reports a command that throws rather than letting it escape', async () => {
    // `registry.runCommand` rethrows, so submit's catch is what stands between a
    // broken command and an unhandled rejection in the click handler.
    minibuffer.open('/mb.boom');
    await expect(minibuffer.submit()).resolves.toBe(false);
    const state = minibuffer.getSnapshot();
    expect(state.open).toBe(false);
    expect(state.echo?.tone).toBe('error');
    expect(state.echo?.text).toContain('nope');
  });
});
