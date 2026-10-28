/**
 * What the frontend may ask of the native browser pane's CDP bridge (cdp.ts).
 *
 * The frontend is trusted, but a compromised frontend should not inherit the whole
 * protocol, so methods are allowlisted: page, DOM, input, script and read-only
 * network — enough for snapshot/click/type/read and the network strip. `Browser.*`
 * (the whole browser process), `Target.*` (attach to other targets, including the
 * app), `Storage.*`, `Security.*`, `Fetch.*` (request interception) and
 * `Emulation.*` (fingerprint changes that read as automation to bot walls) stay out.
 */
import { parseWebUrl } from './urls.js';

const METHOD_DOMAINS = ['Page', 'Runtime', 'DOM', 'Input', 'Accessibility'];
const METHODS = ['Network.enable', 'Network.disable', 'Network.getResponseBody'];
/** Events are read-only, so the bar is lower than for methods. */
const EVENT_DOMAINS = ['Page', 'Runtime', 'DOM', 'Network'];

function split(name: string): [string, string] | null {
  const dot = name.indexOf('.');
  if (dot <= 0 || dot === name.length - 1) return null;
  return [name.slice(0, dot), name.slice(dot + 1)];
}

export function methodAllowed(method: string): boolean {
  const parts = split(method);
  if (!parts) return false;
  return METHOD_DOMAINS.includes(parts[0]) || METHODS.includes(method);
}

export function eventAllowed(event: string): boolean {
  const parts = split(event);
  return parts !== null && EVENT_DOMAINS.includes(parts[0]);
}

/**
 * `Page.navigate` gets the same scheme guard as `navigate_browser_webview`: CDP would
 * otherwise happily point the pane at `file:` or `javascript:`. Throws if refused.
 */
export function checkParams(method: string, params: Record<string, unknown>): void {
  if (method === 'Page.navigate') {
    const url = typeof params.url === 'string' ? params.url : '';
    parseWebUrl(url);
  }
}
