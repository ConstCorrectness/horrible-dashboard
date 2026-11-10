import { beforeEach, describe, expect, it } from 'vitest';

import type { ElementInfo } from '../action-gate';
import {
  bindRefDocForTests,
  browserAgentTools,
  pointerArgs,
  resetRefDocsForTests,
  staleRef,
} from '../agentTools';

const info = (doc: string): ElementInfo =>
  ({ tag: 'button', name: 'Go', host: 'a.test', doc }) as unknown as ElementInfo;

beforeEach(() => resetRefDocsForTests());

describe('refs bound to their document', () => {
  it('refuses a ref that names nothing', () => {
    expect(staleRef('server', 4, null)).toMatch(/no element with ref 4/);
  });

  it('accepts a ref from the page it was handed out in', () => {
    bindRefDocForTests('server', 'doc-a');
    expect(staleRef('server', 4, info('doc-a'))).toBeNull();
  });

  it('refuses the same number on a page that replaced it', () => {
    bindRefDocForTests('server', 'doc-a');
    expect(staleRef('server', 4, info('doc-b'))).toMatch(/since been replaced/);
  });

  it('keeps pages apart, and trusts a page it never handed refs out for', () => {
    bindRefDocForTests('native-1', 'doc-a');
    expect(staleRef('native-2', 4, info('doc-z'))).toBeNull();
    expect(staleRef('native-1', 4, info('doc-z'))).not.toBeNull();
  });
});

describe('pointer arguments', () => {
  it('takes move and click at a point, and a drag with an end', () => {
    expect(pointerArgs({ action: 'click', x: 10, y: 20 })).toEqual({
      action: 'click',
      x: 10,
      y: 20,
    });
    expect(pointerArgs({ action: 'drag', x: 1, y: 2, toX: 3, toY: 4 })).toEqual({
      action: 'drag',
      x: 1,
      y: 2,
      toX: 3,
      toY: 4,
    });
  });

  it('refuses anything else rather than guessing', () => {
    expect(pointerArgs({ action: 'drag', x: 1, y: 2 })).toBeNull();
    expect(pointerArgs({ action: 'click', x: '10', y: 20 })).toBeNull();
    expect(pointerArgs({ action: 'click', x: Number.NaN, y: 20 })).toBeNull();
    expect(pointerArgs({ action: 'wiggle', x: 1, y: 2 })).toBeNull();
  });
});

describe('reach tools', () => {
  const byName = (n: string) => browserAgentTools.find((t) => t.name === n);

  it('find and screenshot only read; pointer acts and is gated per call', () => {
    expect(byName('browser.find')?.sideEffect).toBe(false);
    expect(byName('browser.screenshot')?.sideEffect).toBe(false);
    expect(byName('browser.pointer')?.sideEffect).toBe(true);
    expect(byName('browser.pointer')?.gate).toBeTypeOf('function');
  });

  it('a pointer call with bad arguments is not asked about: it acts on nothing', async () => {
    const gate = await byName('browser.pointer')!.gate!({ action: 'click', x: 'here' });
    expect(gate.sideEffect).toBe(false);
  });
});
