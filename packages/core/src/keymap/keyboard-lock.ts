/**
 * **Keyboard Lock** — the only mechanism a web view has for taking keys the host
 * would otherwise keep.
 *
 * Split out of `dispatch.ts` so `state.ts` can ask whether the lock is live when
 * it builds the `KeyContext` (`reserved.ts` needs it to stop calling `alt+tab`
 * unreachable) without the two modules importing each other.
 *
 * The API is Chromium-only and, per spec, active only in **JavaScript-initiated
 * document fullscreen** — F11 and a native shell's borderless window do not
 * qualify, which is why `setDocumentFullscreen` in `../fullscreen.ts` exists.
 *
 * See docs/architecture/keybindings.mdx.
 */
import { getSetting } from '../settings';
import { captureStore, getCapture } from './capture';

/** Setting gating system-key capture. Declared in modules/keymap. */
export const CAPTURE_SYSTEM_KEYS_KEY = 'keymap.captureSystemKeys';

/**
 * Can the page keep Escape while a pointer/keyboard capture is held?
 *
 * Only with the Keyboard Lock API, which is Chromium-only **and** requires
 * document fullscreen. Everywhere else the browser releases pointer lock on
 * Escape no matter what we do, so a `passthrough` policy has to degrade to
 * `release` — and the HUD has to say so, rather than promising a gesture that
 * won't work.
 */
export function canHoldEscape(): boolean {
  if (typeof navigator === 'undefined' || typeof document === 'undefined') return false;
  const keyboard = (navigator as Navigator & { keyboard?: { lock?: unknown } }).keyboard;
  return typeof keyboard?.lock === 'function' && document.fullscreenElement !== null;
}

/**
 * Will the **host** act on Escape before the page can keep it?
 *
 * Only while it has something of its own to undo — pointer lock or document
 * fullscreen — and Keyboard Lock can't hold the key back. A windowed editor with
 * no pointer lock is *not* in that position: the page owns Escape outright, so a
 * `passthrough` capture needs no degrading there. Treating every non-fullscreen
 * host as hostile used to swallow an editor's Escape to release capture.
 */
export function hostTakesEscape(): boolean {
  if (typeof document === 'undefined') return false;
  const hostHasSomething =
    document.pointerLockElement != null || document.fullscreenElement != null;
  return hostHasSomething && !canHoldEscape();
}

/** Ask the host to route Escape to the page. No-op where unsupported. */
export async function lockEscape(): Promise<void> {
  const keyboard = (
    navigator as Navigator & { keyboard?: { lock?: (keys: string[]) => Promise<void> } }
  ).keyboard;
  try {
    await keyboard?.lock?.(['Escape']);
  } catch {
    /* unsupported or not fullscreen — the ladder degrades to 'release' */
  }
}

/**
 * Can the page take the keys the **OS** normally owns — `alt+tab`, `alt+f4`?
 *
 * Same platform requirement as {@link canHoldEscape} (Keyboard Lock, document
 * fullscreen) plus two of our own: the focused pane must have declared
 * `systemKeys` on its capture, and the user must have turned the setting on. It
 * is off by default because a window that swallows `alt+tab` without saying so is
 * indistinguishable from a hung machine.
 *
 * Note what is *not* achievable at any setting: `ctrl+alt+del` and the platform's
 * other secure-attention sequences are reserved below the browser. Counter-Strike
 * does not take `alt+tab` either — exclusive fullscreen owns the *display*, not
 * the keyboard — so this is strictly more invasive than the game it imitates.
 */
export function canHoldSystemKeys(): boolean {
  if (!canHoldEscape()) return false;
  if (!getCapture()?.systemKeys) return false;
  return getSetting<boolean>(CAPTURE_SYSTEM_KEYS_KEY) === true;
}

/**
 * Take the keyboard as far as this host allows.
 *
 * With every gate open that is *every* key Keyboard Lock will give us (`lock()`
 * with no argument list); otherwise it degrades to the Escape-only lock, which is
 * what the hold-to-release gesture needs and is the behaviour that shipped
 * before. Callers do not branch — they ask for the most and get what is legal.
 */
export async function lockSystemKeys(): Promise<void> {
  if (!canHoldSystemKeys()) return lockEscape();
  const keyboard = (
    navigator as Navigator & { keyboard?: { lock?: (keys?: string[]) => Promise<void> } }
  ).keyboard;
  try {
    await keyboard?.lock?.();
  } catch {
    // Refused mid-flight (left fullscreen between the check and the call). Fall
    // back rather than leaving the pane with no Escape handling at all.
    await lockEscape();
  }
}

export function unlockEscape(): void {
  const keyboard = (navigator as Navigator & { keyboard?: { unlock?: () => void } }).keyboard;
  try {
    keyboard?.unlock?.();
  } catch {
    /* nothing was locked */
  }
}

/**
 * Hold the Escape lock for as long as a `passthrough` capture is held.
 *
 * In document fullscreen the host exits fullscreen on Escape before the page
 * sees the key, so without this an editor's Escape "windowed" the whole
 * dashboard instead of reaching the editor. Only the game used to take the lock,
 * and only once pointer-locked. Now the lock follows the capture store, so every
 * pane that declares `escape: 'passthrough'` (editors, terminals, notebooks) gets
 * its Escape, and the ladder's hold-to-release still works as the way out.
 *
 * Re-evaluated on `fullscreenchange` too: the lock only takes effect in document
 * fullscreen, and the system-key gates depend on it. Returns an uninstaller.
 */
export function followCaptureWithEscapeLock(): () => void {
  if (typeof navigator === 'undefined' || typeof document === 'undefined') return () => {};
  let locked = false;
  const sync = (): void => {
    const wants = getCapture()?.escape === 'passthrough';
    if (wants) {
      locked = true;
      void lockSystemKeys();
    } else if (locked) {
      locked = false;
      unlockEscape();
    }
  };
  const unsubscribe = captureStore.subscribe(sync);
  document.addEventListener('fullscreenchange', sync);
  sync();
  return () => {
    unsubscribe();
    document.removeEventListener('fullscreenchange', sync);
    if (locked) unlockEscape();
  };
}
