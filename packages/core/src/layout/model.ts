/**
 * Ids and tab-list edits shared by every layout layer, plus the frame lookups
 * under the names the rest of the app has always imported them by.
 *
 * Every function is immutable and deterministic — id allocation draws from the
 * caller-supplied monotonic `seq` (`FrameState.paneSeq`), so a caller holding a
 * snapshot can precompute the ids an operation will assign. No React, no
 * registry, no DOM. The frame-level work lives in `dock/frame.ts`; the aliases
 * at the bottom keep `findPaneAnywhere` and friends meaning "wherever the pane
 * is", whatever that now is.
 */
import type { AreaNode } from './types';

export function areaId(seq: number): string {
  return `a${seq}`;
}

export function instanceId(viewId: string, seq: number): string {
  return `${viewId}#${seq}`;
}

export function createArea(id: string): AreaNode {
  return { kind: 'area', id, tabs: [], activeTab: 0 };
}

/** Null when `index` is out of range — the reducer's "no change" signal. */
export function setActiveTab(area: AreaNode, index: number): AreaNode | null {
  if (index < 0 || index >= area.tabs.length) return null;
  return { ...area, activeTab: index };
}

/**
 * Move one tab within its area, splice-style: the tab at `from` is pulled out and
 * re-inserted so it ends up at `to` in the *resulting* list.
 *
 * `activeTab` is carried by identity, not by index — it is a position, and every
 * naive reorder bug is the same one: the user drags a background tab past the
 * active one and the group silently switches to a different pane. Which pane is
 * on screen must not change because the strip was rearranged.
 */
export function reorderTab(area: AreaNode, from: number, to: number): AreaNode | null {
  const n = area.tabs.length;
  if (from < 0 || from >= n || to < 0 || to >= n || from === to) return null;
  const active = area.tabs[area.activeTab];
  const tabs = [...area.tabs];
  const [moved] = tabs.splice(from, 1);
  tabs.splice(to, 0, moved);
  const activeTab = active ? tabs.indexOf(active) : area.activeTab;
  return { ...area, tabs, activeTab: activeTab >= 0 ? activeTab : 0 };
}

export {
  createEmptyDockFrame as createEmptyFrame,
  findAreaAnywhere,
  findPane as findPaneAnywhere,
  listPanes,
  updateAreaAnywhere,
  updatePane as updatePaneAnywhere,
  visiblePanes,
} from './dock/frame';
