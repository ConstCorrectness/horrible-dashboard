// @vitest-environment happy-dom
//
// The bridge is a real capture-phase `document` listener, so this needs a DOM.
// The default core environment has none (see modules/editor/__tests__/lsp.test.ts
// for the other file that opts in).
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  installExternalLinkBridge,
  notifyExternalOpenFailed,
  onExternalOpenFailed,
  openExternal,
} from '../external';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('openExternal', () => {
  it('reports failure when the pop-up blocker eats the window', async () => {
    vi.stubGlobal('window', { open: () => null });
    await expect(openExternal('https://example.com')).resolves.toBe(false);
  });

  it('reports success when a window comes back', async () => {
    vi.stubGlobal('window', { open: () => ({}) });
    await expect(openExternal('https://example.com')).resolves.toBe(true);
  });

  it('reports failure when the desktop shell rejects the invoke', async () => {
    // Exactly the shape of the bug this suite exists for: the Tauri ACL rejects
    // `open_external` when no `allow-open-external` permission is granted, and
    // the rejection is the only signal anything went wrong.
    const internals = { invoke: () => Promise.reject(new Error('not allowed')) };
    vi.stubGlobal('window', { __TAURI_INTERNALS__: internals });
    await expect(openExternal('https://example.com')).resolves.toBe(false);
  });
});

describe('the failure sink', () => {
  it('delivers to subscribers and stops after unsubscribe', () => {
    const seen: string[] = [];
    const off = onExternalOpenFailed((url) => seen.push(url));
    notifyExternalOpenFailed('https://a.example');
    off();
    notifyExternalOpenFailed('https://b.example');
    expect(seen).toEqual(['https://a.example']);
  });
});

describe('installExternalLinkBridge', () => {
  /** A click on an anchor, routed through a real capture-phase listener. */
  function clickAnchor(href: string): { defaultPrevented: boolean } {
    const anchor = document.createElement('a');
    anchor.href = href;
    document.body.appendChild(anchor);
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    anchor.dispatchEvent(event);
    anchor.remove();
    return { defaultPrevented: event.defaultPrevented };
  }

  it('announces a failure instead of swallowing it', async () => {
    // The regression under test: the bridge cancels the browser's own navigation
    // and then substitutes an open that failed. Discarding that failure — which
    // it used to — leaves the click gone with nothing on screen and no error.
    const open = vi.fn(() => null);
    vi.stubGlobal('open', open);
    const failures: string[] = [];
    const off = onExternalOpenFailed((url) => failures.push(url));

    installExternalLinkBridge();
    const { defaultPrevented } = clickAnchor('https://example.com/docs');

    expect(defaultPrevented).toBe(true);
    expect(open).toHaveBeenCalled();
    // The open is awaited, so let the microtask queue drain.
    await Promise.resolve();
    await Promise.resolve();
    expect(failures).toEqual(['https://example.com/docs']);
    off();
  });
});
