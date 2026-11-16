/**
 * THE layout store — one observable atom holding the active workspace id and
 * its frame, mutated only through the reducer below. UI renders from it via
 * `useSyncExternalStore`, the controller dispatches into it, and persistence
 * subscribes to autosave it. A no-op action returns the same snapshot reference
 * so subscribers don't re-render. Deterministic id allocation (see model.ts)
 * means a dispatcher holding the pre-dispatch snapshot can precompute the ids
 * an action will create (`instanceId(view, frame.paneSeq)`).
 *
 * The frame verbs themselves live in `dock/reducer.ts`; this adds the workspace
 * swap and the revision counter around them.
 */
import type { LayoutAction } from './actions';
import { createEmptyDockFrame, sanitize } from './dock/frame';
import { dockReduce } from './dock/reducer';
import type { FrameState, LayoutStoreState } from './types';

function reduceFrame(frame: FrameState, action: LayoutAction): FrameState {
  switch (action.type) {
    case 'LOAD_WORKSPACE':
      return sanitize(action.frame);
    case 'CLEAR_FOCUS':
      return frame.focusedInstanceId === null ? frame : { ...frame, focusedInstanceId: null };
    default:
      return dockReduce(frame, action);
  }
}

function reduce(state: LayoutStoreState, action: LayoutAction): LayoutStoreState {
  if (action.type === 'LOAD_WORKSPACE') {
    return {
      workspaceId: action.workspaceId,
      frame: reduceFrame(state.frame, action),
      hydrated: true,
      revision: state.revision, // loads are not user edits — autosave stays clean
    };
  }
  const frame = reduceFrame(state.frame, action);
  if (frame === state.frame) return state;
  // Ways of looking, not layout: the store still re-renders for them, but they are
  // not serialized, so they must not dirty the workspace either.
  const persistent =
    action.type !== 'SET_PRESENTED' &&
    action.type !== 'TOGGLE_MAXIMIZED' &&
    action.type !== 'CLEAR_FOCUS';
  return { ...state, frame, revision: persistent ? state.revision + 1 : state.revision };
}

const initialState: LayoutStoreState = {
  workspaceId: null,
  frame: createEmptyDockFrame(),
  hydrated: false,
  revision: 0,
};

let state = initialState;
const listeners = new Set<() => void>();

export const layoutStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  /** Stable reference between dispatches — safe for useSyncExternalStore. */
  getSnapshot(): LayoutStoreState {
    return state;
  },
  /** Apply an action; returns the (possibly unchanged) new snapshot. */
  dispatch(action: LayoutAction): LayoutStoreState {
    const next = reduce(state, action);
    if (next !== state) {
      state = next;
      for (const listener of listeners) listener();
    }
    return state;
  },
  /** Test-only: reset to the pristine boot state. */
  resetForTests(): void {
    state = initialState;
    for (const listener of listeners) listener();
  },
};
