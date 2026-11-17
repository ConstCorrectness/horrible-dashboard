/**
 * Actions the layout store's reducer understands: the workbench's own verbs
 * (`dock/reducer.ts`) plus the two the store answers itself. These are
 * mechanical layout edits — region declarations, singleton rules and where a
 * view belongs are resolved by the controller (controller.ts) before dispatch,
 * so the reducer never needs the registry. Everything that mutates bumps
 * `revision` (the autosave dirty check); `LOAD_WORKSPACE` alone replaces state
 * without marking it dirty.
 */
import type { DockAction } from './dock/reducer';
import type { FrameState } from './types';

export type LayoutAction =
  /** Atomically swap in a workspace (id + frame together — never separately). */
  | { type: 'LOAD_WORKSPACE'; workspaceId: string; frame: FrameState }
  /** Focus left every pane (the user clicked the empty backdrop). */
  | { type: 'CLEAR_FOCUS' }
  | DockAction;
