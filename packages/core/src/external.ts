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

/**
 * Open `url` in the user's default browser (new tab in the browser layout, the
 * system browser under the desktop shell). http/https only — the shell enforces
 * it too.
 *
 * **Returns whether it actually opened**, and that return value is the whole
 * point. Both underlying mechanisms fail *quietly*: `window.open` returns `null`
 * when the pop-up blocker eats it, and the Tauri command rejects if the shell
 * predates it. Neither throws anything a caller would notice by default, so a
 * caller that ignored the result — as every caller here once did — turned a
 * blocked pop-up into nothing happening at all. That is survivable for a docs
 * link and not survivable for an OAuth flow, which then sits and polls for
 * fifteen minutes for a page the user was never shown.
 *
 * Callers that merely want a link opened can still ignore the result; callers
 * that need the user to *arrive* somewhere must check it.
 */
export async function openExternal(url: string): Promise<boolean> {
  const invoke = tauriInvoke();
  if (invoke) {
    try {
      await invoke('open_external', { url });
      return true;
    } catch {
      // An older shell without the command, or the OS refusing to launch a
      // browser. Either way the user is not looking at the page.
      return false;
    }
  }
  return window.open(url, '_blank', 'noopener') !== null;
}

/**
 * Show a local **directory** in the OS file manager. Desktop shell only.
 *
 * Returns false in the browser layout rather than throwing, because a web page
 * genuinely cannot do this and the caller's job is to offer something else (the
 * Storage settings section falls back to copying the path). The shell refuses
 * anything that is not a directory, so a rejection here can also mean the folder
 * has not been created yet — which is why the caller checks `exists` first and
 * treats the boolean as "the user is now looking at it", exactly as
 * {@link openExternal} does.
 */
export async function openPath(path: string): Promise<boolean> {
  const invoke = tauriInvoke();
  if (!invoke) return false;
  try {
    await invoke('open_path', { path });
    return true;
  } catch {
    return false;
  }
}

/**
 * Subscribers notified when a URL could not be opened for the user at all.
 *
 * This exists because the *last* fallback needs one too. Every other layer here
 * hands the job down to something else — popup to system browser, system browser
 * to a link the user clicks themselves — and the bottom of that ladder is a link
 * click that also silently failed. At that point the only honest move left is to
 * put the URL on screen and let the user carry it to a browser by hand.
 */
type ExternalOpenFailedListener = (url: string) => void;

const failureListeners = new Set<ExternalOpenFailedListener>();

/** Subscribe to "nothing opened". Returns an unsubscribe. */
export function onExternalOpenFailed(listener: ExternalOpenFailedListener): () => void {
  failureListeners.add(listener);
  return () => failureListeners.delete(listener);
}

/** Announce that `url` could not be opened. Safe to call with no subscribers. */
export function notifyExternalOpenFailed(url: string): void {
  for (const listener of failureListeners) listener(url);
}

/**
 * Route every external-link click in the document through {@link openExternal}.
 * Installed once by the desktop boot (`apps/web/main.tsx` under Tauri): without it,
 * every plain `<a href="https://…">` in the app — docs links, the sign-in card's
 * fallback link, the Ollama install link — is dead on desktop. Same-origin
 * navigation and non-http(s) schemes are left alone. Capture phase, so it wins
 * even inside panels that stop propagation on bubble.
 *
 * **It must report failure.** This handler cancels the browser's own navigation
 * before substituting its own, so when the substitute fails the click is simply
 * gone. Ignoring {@link openExternal}'s result — as this did — converted one
 * missing Tauri ACL entry (`allow-open-external`, absent from
 * `capabilities/default.json` and `permissions/window.toml` since the command was
 * added) into *every* external link in the desktop app doing nothing, with no
 * error anywhere. The docblock above already said callers who need the user to
 * arrive somewhere must check the boolean; this was the caller that didn't.
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
      void openExternal(parsed.href).then((opened) => {
        if (!opened) notifyExternalOpenFailed(parsed.href);
      });
    },
    true,
  );
}
