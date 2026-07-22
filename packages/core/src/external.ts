/**
 * Opening URLs in the user's **default browser**, on either layout.
 *
 * The browser layout just uses `window.open`. Under the Tauri desktop shell the
 * webview cannot spawn browser windows at all — `window.open`, `target="_blank"`,
 * and "open link in new window" are silent no-ops — so external URLs are handed to
 * the OS via the shell's `open_external` command (apps/desktop/src-tauri/window.rs).
 *
 * OAuth flows MUST route through this rather than `window.open` directly: the
 * system browser carries the user's existing sessions and password manager, and
 * Google rejects OAuth inside embedded webviews outright (RFC 8252).
 *
 * Detection is the internals marker rather than a capability entry because this
 * isn't a feature that's present or absent — both layouts can open a URL — it's
 * purely *how*, which only the runtime can know.
 */

interface TauriInternals {
  invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
}

/** True when running inside the Tauri desktop shell (vs the browser layout). */
export function isDesktopShell(): boolean {
  return '__TAURI_INTERNALS__' in window;
}

function tauriInvoke(): TauriInternals['invoke'] | null {
  const internals = (window as { __TAURI_INTERNALS__?: TauriInternals }).__TAURI_INTERNALS__;
  return internals?.invoke?.bind(internals) ?? null;
}

/** Open `url` in the user's default browser (new tab in the browser layout, the
 * system browser under the desktop shell). http/https only — the shell enforces it
 * too. */
export async function openExternal(url: string): Promise<void> {
  const invoke = tauriInvoke();
  if (invoke) {
    await invoke('open_external', { url });
    return;
  }
  window.open(url, '_blank', 'noopener');
}

/**
 * Route every external-link click in the document through {@link openExternal}.
 * Installed once by the desktop boot (`apps/web/main.tsx` under Tauri): without it,
 * every plain `<a href="https://…">` in the app — docs links, the sign-in card's
 * fallback link, the Ollama install link — is dead on desktop. Same-origin
 * navigation and non-http(s) schemes are left alone. Capture phase, so it wins
 * even inside panels that stop propagation on bubble.
 */
export function installExternalLinkBridge(): void {
  document.addEventListener(
    'click',
    (event) => {
      if (event.defaultPrevented) return;
      const anchor = (event.target as HTMLElement | null)?.closest?.('a[href]');
      if (!anchor) return;
      let parsed: URL;
      try {
        parsed = new URL((anchor as HTMLAnchorElement).href, window.location.href);
      } catch {
        return;
      }
      if (parsed.origin === window.location.origin) return;
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return;
      event.preventDefault();
      void openExternal(parsed.href);
    },
    true,
  );
}
