/**
 * The shell's single keydown handler, and the **Escape ladder**.
 *
 * Escape used to be claimed independently by three places — the dialog layer
 * (capture phase), the frame's area-fullscreen exit (bubble phase), and the
 * browser's own pointer-lock release — with no ordering between them. Pressing
 * Escape in a fullscreened area with a dialog open did two things at once. Here
 * it is one ordered ladder, first rung wins.
 *
 * See docs/architecture/keybindings.mdx.
 */
import { getCapture, releaseCapture } from './capture';
import { getKeymap, readKeyContext } from './state';
import { resolveKey } from './resolve';
import { isModifierEvent, type Chord } from './spec';

/** How long Escape must be held to release a `passthrough` capture. */
export const DEFAULT_ESCAPE_HOLD_MS = 400;

/** How long a chord prefix waits for its next stroke before giving up. */
const CHORD_TIMEOUT_MS = 1500;

export interface KeymapHooks {
  runCommand: (id: string) => void;
  /** Dismiss the active modal dialog. Returns true if there was one. */
  dismissDialog: () => boolean;
  /** Exit in-window area fullscreen. Returns true if it was on. */
  exitFullscreen: () => boolean;
  /** Close any open popover/menu. Returns true if something closed. */
  closeTransient: () => boolean;
  /** Show/hide the "waiting for the next key" hint. */
  setPendingChord: (keys: string | null) => void;
  /** Tell the user how to get the mouse back, once, when capture starts. */
  notifyCaptureHint?: (message: string) => void;
  escapeHoldMs?: () => number;
}

let hooks: KeymapHooks | null = null;
let pending: KeyboardEvent[] = [];
let pendingTimer: ReturnType<typeof setTimeout> | null = null;
let escapeHeldSince: number | null = null;
let escapeHoldTimer: ReturnType<typeof setTimeout> | null = null;

function clearPending(): void {
  pending = [];
  if (pendingTimer) clearTimeout(pendingTimer);
  pendingTimer = null;
  hooks?.setPendingChord(null);
}

function describePending(): string {
  return pending.map((e) => e.key).join(' ');
}

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

export function unlockEscape(): void {
  const keyboard = (navigator as Navigator & { keyboard?: { unlock?: () => void } }).keyboard;
  try {
    keyboard?.unlock?.();
  } catch {
    /* nothing was locked */
  }
}

/** The Escape ladder. Returns true when a rung consumed the key. */
function handleEscape(): boolean {
  if (!hooks) return false;

  // 1. A half-typed chord — Escape abandons it before anything else, so it can
  //    never leak into a rung below.
  if (pending.length > 0) {
    clearPending();
    return true;
  }

  // 2. Modal dialogs outrank everything: they are the thing in front of you.
  if (hooks.dismissDialog()) return true;

  // 3/4. A capturing pane.
  const capture = getCapture();
  if (capture) {
    if (capture.escape === 'release' || !canHoldEscape()) {
      releaseCapture(capture.instanceId);
      return true;
    }
    // 'passthrough' with Keyboard Lock: the pane gets this Escape, and holding
    // releases. The timer is what distinguishes a tap from a hold — started on
    // the first keydown, cancelled by keyup.
    if (escapeHeldSince === null) {
      escapeHeldSince = Date.now();
      const holdMs = hooks.escapeHoldMs?.() ?? DEFAULT_ESCAPE_HOLD_MS;
      escapeHoldTimer = setTimeout(() => {
        releaseCapture(capture.instanceId);
        escapeHeldSince = null;
      }, holdMs);
    }
    return false; // deliver the tap to the pane
  }

  // 5. In-window area fullscreen.
  if (hooks.exitFullscreen()) return true;

  // 6. Popovers, context menus, transient chrome.
  if (hooks.closeTransient()) return true;

  return false;
}

function onKeyUp(e: KeyboardEvent): void {
  if (e.key !== 'Escape') return;
  if (escapeHoldTimer) clearTimeout(escapeHoldTimer);
  escapeHoldTimer = null;
  escapeHeldSince = null;
}

function onKeyDown(e: KeyboardEvent): void {
  if (!hooks) return;
  // A bare modifier press never resolves anything and must not break a chord.
  if (isModifierEvent(e)) return;

  if (e.key === 'Escape') {
    if (handleEscape()) {
      e.preventDefault();
      e.stopPropagation();
    }
    return;
  }

  const ctx = readKeyContext();

  // Typing wins over unmodified bindings. `mod+`/`alt+` chords still reach the
  // shell — that is how mod+s saves from inside a buffer. Note alt is included
  // deliberately: it used to be excluded, so alt+x opened the minibuffer while
  // the user was mid-word in a text field.
  const bare = !e.ctrlKey && !e.metaKey && !e.altKey;
  if (bare && ctx.textInput && pending.length === 0) return;

  const result = resolveKey(e, ctx, getKeymap(), pending);
  if (result.kind === 'pending') {
    pending = [...pending, e];
    hooks.setPendingChord(describePending());
    if (pendingTimer) clearTimeout(pendingTimer);
    pendingTimer = setTimeout(clearPending, CHORD_TIMEOUT_MS);
    e.preventDefault();
    return;
  }
  if (result.kind === 'command') {
    clearPending();
    e.preventDefault();
    hooks.runCommand(result.command);
    return;
  }
  // Nothing matched. A stroke that started a sequence and then went nowhere
  // ends the sequence rather than silently swallowing the next key too.
  if (pending.length > 0) clearPending();
}

/**
 * Install the shell's key handling. Capture phase, so the ladder outranks any
 * component-level Escape handler still in the tree.
 */
export function installKeymap(next: KeymapHooks): () => void {
  hooks = next;
  window.addEventListener('keydown', onKeyDown, true);
  window.addEventListener('keyup', onKeyUp, true);
  return () => {
    window.removeEventListener('keydown', onKeyDown, true);
    window.removeEventListener('keyup', onKeyUp, true);
    clearPending();
    hooks = null;
  };
}

/** Test seam: the prefix the dispatcher is currently holding. */
export function pendingChord(): readonly KeyboardEvent[] {
  return pending;
}

export type { Chord };
