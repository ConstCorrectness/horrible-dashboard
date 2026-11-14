import { describe, expect, it } from 'vitest';

import {
  CRASH_WINDOW_MS,
  CrashPolicy,
  MAX_CRASHES,
  crashPageHtml,
  describeCrash,
} from '../src/crashPolicy';

describe('CrashPolicy', () => {
  it('reloads at once, then backs off', () => {
    const p = new CrashPolicy();
    expect(p.onCrash(0)).toEqual({ action: 'reload', delayMs: 0 });
    expect(p.onCrash(1_000)).toEqual({ action: 'reload', delayMs: 2_000 });
    expect(p.onCrash(5_000)).toEqual({ action: 'reload', delayMs: 5_000 });
  });

  it('gives up on a page that keeps crashing — not a dead window, an error page', () => {
    const p = new CrashPolicy();
    let last;
    for (let i = 0; i < MAX_CRASHES; i++) last = p.onCrash(i * 1_000);
    expect(last).toEqual({ action: 'give-up' });
  });

  it('forgets crashes that are old enough', () => {
    const p = new CrashPolicy();
    for (let i = 0; i < MAX_CRASHES - 1; i++) p.onCrash(i);
    expect(p.onCrash(CRASH_WINDOW_MS * 2)).toEqual({ action: 'reload', delayMs: 0 });
  });

  it('a second crash soon after the first is retried (the old handler left it dead)', () => {
    const p = new CrashPolicy();
    p.onCrash(0);
    expect(p.onCrash(3_000).action).toBe('reload');
  });
});

describe('crash reporting', () => {
  it('names the reason and exit code', () => {
    expect(describeCrash({ reason: 'oom', exitCode: -536870904 })).toBe(
      'renderer process gone: reason=oom exitCode=-536870904',
    );
  });

  it('escapes what it renders', () => {
    const html = crashPageHtml({ reason: '<x>', exitCode: 1 }, 'http://127.0.0.1:5173/?a="b"');
    expect(html).not.toContain('<x>');
    expect(html).toContain('&#60;x&#62;');
    expect(html).not.toContain('"b"');
  });
});
