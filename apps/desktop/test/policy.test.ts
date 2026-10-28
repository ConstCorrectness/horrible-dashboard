import { describe, expect, it } from 'vitest';

import { toAccelerator } from '../src/accelerator';
import { checkParams, eventAllowed, methodAllowed } from '../src/cdpPolicy';
import { isWebUrl, parseWebUrl, sanitizeBounds, wantsPopupWindow } from '../src/urls';

describe('accelerators', () => {
  it('maps mod to CommandOrControl', () => {
    expect(toAccelerator('mod+k')).toBe('CommandOrControl+K');
    expect(toAccelerator('mod+shift+space')).toBe('CommandOrControl+Shift+Space');
    expect(toAccelerator('ctrl+alt+arrowleft')).toBe('Control+Alt+Left');
    expect(toAccelerator('meta+f1')).toBe('Super+F1');
  });

  // A `code:` spec names a physical key, which has no accelerator spelling —
  // registering its letter would bind the wrong key.
  it('rejects positional specs and unknown modifiers', () => {
    expect(toAccelerator('mod+code:KeyW')).toBeNull();
    expect(toAccelerator('hyper+k')).toBeNull();
    expect(toAccelerator('')).toBeNull();
  });
});

describe('CDP allowlist', () => {
  it('allows page, DOM, input, script and read-only network', () => {
    for (const m of [
      'Page.navigate',
      'Runtime.evaluate',
      'DOM.getDocument',
      'Input.dispatchMouseEvent',
    ]) {
      expect(methodAllowed(m)).toBe(true);
    }
    expect(methodAllowed('Network.enable')).toBe(true);
    expect(methodAllowed('Network.getResponseBody')).toBe(true);
  });

  it('keeps the dangerous domains out', () => {
    for (const m of [
      'Browser.close',
      'Target.attachToTarget',
      'Storage.getCookies',
      'Network.getAllCookies',
      'Fetch.enable',
      'Emulation.setUserAgentOverride',
      'Page',
      'Page.',
      '.navigate',
    ]) {
      expect(methodAllowed(m)).toBe(false);
    }
  });

  it('allows events from observed domains only', () => {
    expect(eventAllowed('Network.requestWillBeSent')).toBe(true);
    expect(eventAllowed('Page.loadEventFired')).toBe(true);
    expect(eventAllowed('Target.attachedToTarget')).toBe(false);
  });

  it('guards Page.navigate like navigate_browser_webview', () => {
    expect(() => checkParams('Page.navigate', { url: 'https://example.com' })).not.toThrow();
    expect(() => checkParams('Page.navigate', { url: 'file:///etc/passwd' })).toThrow();
    expect(() => checkParams('Page.navigate', { url: 'javascript:alert(1)' })).toThrow();
    expect(() => checkParams('Page.navigate', {})).toThrow();
  });
});

describe('urls', () => {
  it('loads only http(s)', () => {
    expect(parseWebUrl('https://example.com').hostname).toBe('example.com');
    expect(isWebUrl('http://example.com')).toBe(true);
    for (const bad of [
      'file:///etc/passwd',
      'app://horrible/index.html',
      'javascript:alert(1)',
      'not a url',
    ]) {
      expect(isWebUrl(bad)).toBe(false);
    }
  });

  it('clamps bounds to whole, non-empty DIPs', () => {
    expect(sanitizeBounds({ x: 10.4, y: 20.6, width: 0, height: -3 })).toEqual({
      x: 10,
      y: 21,
      width: 1,
      height: 1,
    });
    expect(sanitizeBounds({ x: NaN, y: Infinity, width: 300.5, height: 200 })).toEqual({
      x: 0,
      y: 0,
      width: 301,
      height: 200,
    });
  });

  it('treats a sized window.open as a popup and the rest as tabs', () => {
    expect(wantsPopupWindow('width=500,height=600')).toBe(true);
    expect(wantsPopupWindow('popup')).toBe(true);
    expect(wantsPopupWindow('')).toBe(false);
    expect(wantsPopupWindow('noopener,noreferrer')).toBe(false);
  });
});
