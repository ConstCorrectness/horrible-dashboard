import { describe, expect, it } from 'vitest';

import { registry } from '../../../registry';
import { browserModule } from '../index';

describe('browser module', () => {
  it('declares the browser.view document panel', () => {
    const panel = browserModule.panels?.find((p) => p.id === 'browser.view');
    expect(panel).toBeDefined();
    expect(panel?.role).toBe('document');
    expect(panel?.singleton).toBeFalsy(); // multi-tab
  });

  it('declares open + focus-url-bar commands and a scoped keybinding', () => {
    const commandIds = (browserModule.commands ?? []).map((c) => c.id);
    expect(commandIds).toContain('browser.open');
    expect(commandIds).toContain('browser.focusUrlBar');
    const kb = browserModule.keybindings?.find((k) => k.command === 'browser.focusUrlBar');
    expect(kb?.scope).toBe('browser.view');
  });

  it('declares home-page + reader-mode + engine settings', () => {
    const keys = (browserModule.settings ?? []).map((s) => s.key);
    expect(keys).toContain('browser.homePage');
    expect(keys).toContain('browser.readerModeDefault');
    expect(keys).toContain('browser.engine');
    const engine = (browserModule.settings ?? []).find((s) => s.key === 'browser.engine');
    expect(engine?.enumValues).toEqual(['auto', 'full', 'iframe']);
  });

  it('exposes read/open + the full-engine scrape/act agent tools', () => {
    const tools = browserModule.panels?.find((p) => p.id === 'browser.view')?.agentTools ?? [];
    const byName = (n: string) => tools.find((t) => t.name === n);
    // browser.read stays read-only; url is now optional (reads the open page in full mode).
    expect(byName('browser.read')?.sideEffect).toBe(false);
    expect(byName('browser.read')?.params?.required).toBeUndefined();
    // browser.open still shows a page in the UI (side-effecting).
    expect(byName('browser.open')?.sideEffect).toBe(true);
    expect(byName('browser.open')?.specifierTemplate).toBe('{url}');
    // Full-engine agentic tools: snapshot (read-only) + click/type/scrape.
    expect(byName('browser.snapshot')?.sideEffect).toBe(false);
    expect(byName('browser.click')?.sideEffect).toBe(true);
    expect(byName('browser.click')?.params?.required).toContain('ref');
    expect(byName('browser.type')?.params?.required).toEqual(['ref', 'text']);
    expect(byName('browser.scrape')?.params?.required).toContain('selector');
  });

  it('registers with the shared registry', () => {
    registry.register(browserModule);
    expect(registry.panels.some((p) => p.id === 'browser.view')).toBe(true);
    expect(registry.commands.some((c) => c.id === 'browser.open')).toBe(true);
  });
});
