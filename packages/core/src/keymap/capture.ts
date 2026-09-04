/**
 * **Input capture** — a pane taking the keyboard (and optionally the mouse) for
 * itself.
 *
 * Before this existed each pane fended for itself: the game preventDefault'ed an
 * allow-list of key codes but never stopped propagation, so the shell's own
 * bindings fired anyway — pressing `t` while pointer-locked toggled a region
 * strip mid-firefight. Capture makes that one rule enforced in one place: while a
 * pane holds the keyboard, the resolver only considers that pane's bindings.
 *
 * Two invariants live here rather than in the panes, because every pane would
 * otherwise have to reimplement them:
 *   - only the **focused** pane can hold capture, and losing focus releases it;
 *   - releasing always runs the pane's `onRelease`, so held keys and pointer lock
 *     unwind exactly once no matter who initiated the release.
 *
 * See docs/architecture/keybindings.mdx.
 */
import { useEffect, useRef, useSyncExternalStore } from 'react';

import { layoutStore } from '../layout/store';
import type { CaptureMode } from './context';

export type { CaptureMode };

/** What a tap of Escape does while this pane holds capture. */
export type EscapePolicy =
  /** Escape releases capture. For panes with no use for the key. */
  | 'release'
  /**
   * Escape is delivered to the pane (a game's own pause menu), and **holding**
   * it releases capture. Degrades to `release` where the host won't let us keep
   * Escape — see `canHoldEscape` in dispatch.ts.
   */
  | 'passthrough';

export interface CaptureRequest {
  mode: CaptureMode;
  escape: EscapePolicy;
  /**
   * This pane wants the OS's own chords too (`alt+tab`). Advisory: it records
   * what the pane asked for, and `lockSystemKeys` decides whether the platform,
   * the fullscreen state and the user's setting actually allow it.
   */
  systemKeys?: boolean;
  /** Pane instance holding it — capture follows focus, so this must be focused. */
  instanceId: string;
  viewId: string;
  /** Run on release, however it was triggered. Must be idempotent. */
  onRelease?: () => void;
}

export interface CaptureState {
  mode: CaptureMode;
  escape: EscapePolicy;
  systemKeys: boolean;
  instanceId: string;
  viewId: string;
}

let held: (CaptureRequest & CaptureState) | null = null;
const listeners = new Set<() => void>();
/** Stable snapshot reference, so useSyncExternalStore doesn't loop. */
let snapshot: CaptureState | null = null;

function emit(): void {
  snapshot = held
    ? {
        mode: held.mode,
        escape: held.escape,
        systemKeys: held.systemKeys ?? false,
        instanceId: held.instanceId,
        viewId: held.viewId,
      }
    : null;
  for (const listener of listeners) listener();
}

export const captureStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  getSnapshot(): CaptureState | null {
    return snapshot;
  },
};

/** Current capture, or null. */
export function getCapture(): CaptureState | null {
  return snapshot;
}

// Capture follows focus, enforced here rather than in each pane: the moment the
// holder stops being the focused pane it gives the keyboard back. This is the
// invariant that stops a backgrounded game from eating keys — the old panel kept
// `document`-level listeners alive whether or not it was the pane you were in.
layoutStore.subscribe(() => {
  if (!held) return;
  if (layoutStore.getSnapshot().frame.focusedInstanceId !== held.instanceId) {
    releaseCapture(held.instanceId);
  }
});

/**
 * Take capture. Refused unless the requesting pane is the focused one — a
 * background pane grabbing the keyboard is the bug this whole module replaces.
 */
export function requestCapture(request: CaptureRequest): boolean {
  const { focusedInstanceId } = layoutStore.getSnapshot().frame;
  if (focusedInstanceId !== request.instanceId) return false;
  if (held && held.instanceId !== request.instanceId) releaseCapture();
  held = { ...request, systemKeys: request.systemKeys ?? false };
  emit();
  return true;
}

/** Release capture. A no-op when `instanceId` isn't the current holder. */
export function releaseCapture(instanceId?: string): void {
  if (!held) return;
  if (instanceId && held.instanceId !== instanceId) return;
  const previous = held;
  // Clear first, so an `onRelease` that re-enters (exitPointerLock fires
  // pointerlockchange synchronously in some engines) sees no capture held.
  held = null;
  emit();
  previous.onRelease?.();
}

/** Reactive capture state, for HUDs and pane chrome. */
export function useCaptureState(): CaptureState | null {
  return useSyncExternalStore(captureStore.subscribe, captureStore.getSnapshot, () => null);
}

export interface CaptureHandle {
  /** Take the keyboard. Returns false when this pane isn't focused. */
  request: () => boolean;
  release: () => void;
  /** Is this pane currently holding capture? */
  active: boolean;
}

/**
 * Pane-side handle. Registers nothing until `request()` is called, and guarantees
 * release on unmount and on focus loss.
 */
export function useCapture(options: {
  mode: CaptureMode;
  escape: EscapePolicy;
  systemKeys?: boolean;
  instanceId: string | null;
  viewId: string;
  onRelease?: () => void;
}): CaptureHandle {
  const { mode, escape, systemKeys, instanceId, viewId } = options;
  // Read the latest onRelease without making request/release change identity.
  const onReleaseRef = useRef(options.onRelease);
  onReleaseRef.current = options.onRelease;

  const capture = useCaptureState();
  const active = !!instanceId && capture?.instanceId === instanceId;

  // Focus-following release is enforced by the store (see above); the pane only
  // has to guarantee release on unmount.
  useEffect(() => {
    if (!instanceId) return;
    return () => releaseCapture(instanceId);
  }, [instanceId]);

  return {
    active,
    request: () =>
      !!instanceId &&
      requestCapture({
        mode,
        escape,
        systemKeys,
        instanceId,
        viewId,
        onRelease: () => onReleaseRef.current?.(),
      }),
    release: () => instanceId && releaseCapture(instanceId),
  };
}
