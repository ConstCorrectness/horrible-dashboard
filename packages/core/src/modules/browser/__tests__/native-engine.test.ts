import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { setWindowControl, type BrowserCdpEvent, type WindowControl } from '../../../window';

vi.mock('../api', () => ({
  pageScripts: vi.fn(async () => ({
    // Formatted the way the repo's prettier leaves the real files: trailing `;`.
    scripts: { snapshot: '() => ({ url: "u", title: "t", elements: [] });', media: '() => 1' },
  })),
  extractArticle: vi.fn(async (url: string, _html: string, title: string) => ({
    url,
    title: title || 'extracted',
    author: null,
    text: 'body text',
  })),
  engineStatus: vi.fn(async () => ({ enabled: false, installed: false })),
}));

const {
  activeNativeTarget,
  nativeEngine,
  nativeTargetPane,
  pressKey,
  registerNativeTarget,
  touchNativeTarget,
  waitForNativeTarget,
} = await import('../native-engine');
const { agentEngine } = await import('../engines');

type Call = { id: string; method: string; params?: Record<string, unknown> };

/** A shell whose CDP bridge records calls and answers from `reply`. */
function fakeShell(reply: (c: Call) => unknown = () => ({})) {
  const calls: Call[] = [];
  const listeners = new Set<(e: BrowserCdpEvent) => void>();
  const control = {
    browserWebview: {
      onEvent: () => () => {},
      cdp: {
        call: vi.fn(async (id: string, method: string, params?: Record<string, unknown>) => {
          calls.push({ id, method, params });
          return reply({ id, method, params });
        }),
        subscribe: vi.fn(async () => {}),
        onEvent: (fn: (e: BrowserCdpEvent) => void) => {
          listeners.add(fn);
          return () => listeners.delete(fn);
        },
        openDevtools: vi.fn(async () => {}),
      },
    },
  } as unknown as WindowControl;
  setWindowControl(control);
  const emit = (e: BrowserCdpEvent) => listeners.forEach((fn) => fn(e));
  return { calls, emit };
}

const releases: (() => void)[] = [];
afterEach(() => {
  releases.splice(0).forEach((r) => r());
  setWindowControl(null);
});

describe('native target registry', () => {
  it('drives the most recently used tab', () => {
    releases.push(registerNativeTarget('p1:t1', 'p1'), registerNativeTarget('p2:t1', 'p2'));
    expect(activeNativeTarget()).toBe('p2:t1');
    touchNativeTarget('p1:t1');
    expect(activeNativeTarget()).toBe('p1:t1');
    expect(nativeTargetPane('p1:t1')).toBe('p1');
  });

  it('forgets a closed tab', () => {
    const release = registerNativeTarget('p1:t1', 'p1');
    release();
    expect(activeNativeTarget()).toBeNull();
    expect(nativeTargetPane('p1:t1')).toBeNull();
  });

  it('hands browser.open the tab that registers next', async () => {
    releases.push(registerNativeTarget('old', 'p0'));
    const waiting = waitForNativeTarget(1000);
    releases.push(registerNativeTarget('new', 'p1'));
    await expect(waiting).resolves.toBe('new');
  });

  it('gives up waiting rather than hanging the agent', async () => {
    vi.useFakeTimers();
    const waiting = waitForNativeTarget(500);
    vi.advanceTimersByTime(600);
    await expect(waiting).resolves.toBeNull();
    vi.useRealTimers();
  });
});

describe('agentEngine', () => {
  beforeEach(() => fakeShell());

  it('prefers the native pane the human is looking at', async () => {
    releases.push(registerNativeTarget('p1:t1', 'p1'));
    expect((await agentEngine())?.kind).toBe('native');
  });

  it('is null with no native tab and the backend engine off', async () => {
    expect(await agentEngine()).toBeNull();
  });
});

describe('nativeEngine', () => {
  it('evaluates the shared snapshot script, tolerating the formatter’s `;`', async () => {
    const { calls } = fakeShell((c) =>
      c.method === 'Runtime.evaluate'
        ? { result: { value: { url: 'u', title: 't', elements: [] } } }
        : {},
    );
    const snap = await nativeEngine('w').snapshot();
    expect(snap.url).toBe('u');
    const expr = String(calls[0].params?.expression);
    expect(expr.startsWith('(() =>')).toBe(true);
    expect(expr).not.toContain(';)');
    expect(expr.endsWith(')()')).toBe(true);
  });

  it('surfaces a page exception instead of returning undefined', async () => {
    fakeShell(() => ({ exceptionDetails: { exception: { description: 'ReferenceError: x' } } }));
    await expect(nativeEngine('w').info()).rejects.toThrow('ReferenceError: x');
  });

  it('clicks by ref with trusted mouse events at the element centre', async () => {
    const { calls } = fakeShell((c) =>
      c.method === 'Runtime.evaluate' ? { result: { value: { x: 40, y: 20 } } } : {},
    );
    await nativeEngine('w').clickRef(7);
    expect(String(calls[0].params?.expression)).toContain('[data-agent-ref="7"]');
    const mouse = calls.filter((c) => c.method === 'Input.dispatchMouseEvent');
    expect(mouse.map((c) => c.params?.type)).toEqual([
      'mouseMoved',
      'mousePressed',
      'mouseReleased',
    ]);
    expect(mouse[1].params).toMatchObject({ x: 40, y: 20, button: 'left', clickCount: 1 });
  });

  it('tells the agent to re-snapshot when a ref is gone', async () => {
    fakeShell((c) => (c.method === 'Runtime.evaluate' ? { result: { value: null } } : {}));
    await expect(nativeEngine('w').clickRef(3)).rejects.toThrow(/snapshot again/);
  });

  it('types by replacing the field contents, like the backend’s fill', async () => {
    const { calls } = fakeShell((c) =>
      c.method === 'Runtime.evaluate' ? { result: { value: { x: 1, y: 1 } } } : {},
    );
    await nativeEngine('w').typeRef(2, 'hello');
    const insert = calls.find((c) => c.method === 'Input.insertText');
    expect(insert?.params).toEqual({ text: 'hello' });
    // The select-all happens before the insert.
    const selectAt = calls.findIndex((c) => String(c.params?.expression ?? '').includes('select'));
    expect(selectAt).toBeGreaterThan(-1);
    expect(selectAt).toBeLessThan(calls.indexOf(insert!));
  });

  it('navigates and waits for the load event', async () => {
    const shell = fakeShell(() => ({ frameId: 'f' }));
    let done = false;
    const nav = nativeEngine('w')
      .navigate('https://example.com')
      .then(() => (done = true));
    await vi.waitFor(() =>
      expect(shell.calls.some((c) => c.method === 'Page.navigate')).toBe(true),
    );
    expect(done).toBe(false);
    shell.emit({ id: 'other', method: 'Page.loadEventFired', params: {} });
    await Promise.resolve();
    expect(done).toBe(false);
    shell.emit({ id: 'w', method: 'Page.loadEventFired', params: {} });
    await nav;
    expect(done).toBe(true);
  });

  it('reports a failed navigation', async () => {
    fakeShell((c) =>
      c.method === 'Page.navigate' ? { errorText: 'net::ERR_NAME_NOT_RESOLVED' } : {},
    );
    await expect(nativeEngine('w').navigate('https://nope.invalid')).rejects.toThrow(
      'ERR_NAME_NOT_RESOLVED',
    );
  });

  it('reads content through the shared extractor', async () => {
    fakeShell((c) =>
      c.method === 'Runtime.evaluate'
        ? { result: { value: { url: 'https://a.test/x', title: 'Doc', html: '<p>x</p>' } } }
        : {},
    );
    const page = await nativeEngine('w').content();
    expect(page).toEqual({
      url: 'https://a.test/x',
      title: 'Doc',
      author: null,
      text: 'body text',
    });
  });

  it('degrades capture so saving falls back to a text ingest', async () => {
    fakeShell();
    await expect(nativeEngine('w').capture()).rejects.toThrow(/backend-engine/);
  });
});

describe('pressKey', () => {
  it('sends Enter with a carriage return so forms submit', async () => {
    const { calls } = fakeShell();
    await pressKey('w', 'Enter');
    expect(calls[0].params).toMatchObject({
      type: 'keyDown',
      key: 'Enter',
      text: '\r',
      windowsVirtualKeyCode: 13,
    });
    expect(calls[1].params).toMatchObject({ type: 'keyUp', key: 'Enter' });
  });

  it('sends non-printing keys as raw key downs', async () => {
    const { calls } = fakeShell();
    await pressKey('w', 'ArrowDown');
    expect(calls[0].params).toMatchObject({ type: 'rawKeyDown', windowsVirtualKeyCode: 40 });
  });

  it('types a single character', async () => {
    const { calls } = fakeShell();
    await pressKey('w', 'a');
    expect(calls[0].params).toMatchObject({ type: 'keyDown', key: 'a', text: 'a' });
  });

  it('rejects keys it cannot express', async () => {
    fakeShell();
    await expect(pressKey('w', 'Hyper')).rejects.toThrow(/unknown key/);
  });
});
