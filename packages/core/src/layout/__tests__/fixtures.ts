/**
 * Building desktop frames for tests: one window per entry, each holding the
 * listed views as tabs. Replaces seeding a tiled preset, which is what the layout
 * tests used to do to get panes on screen.
 */
import { createEmptyFrame, instanceId } from '../model';
import { layoutStore } from '../store';
import type { FrameState, PaneState, WindowState } from '../types';

/** A window's contents: one view id, or several (its tabs, first showing). */
export type WindowSpec = string | string[];

/**
 * A frame with one window per spec, cascaded so their rects differ. Instance ids
 * are `<view>#<n>` in order; window and area ids follow, as the reducer would mint
 * them.
 */
export function windowsFrame(specs: WindowSpec[], base: Partial<FrameState> = {}): FrameState {
  let seq = 1;
  const windows: WindowState[] = specs.map((spec, i) => {
    const tabs: PaneState[] = [spec]
      .flat()
      .map((viewId) => ({ instanceId: instanceId(viewId, seq++), viewId }));
    const id = `w${seq++}`;
    const area = { kind: 'area' as const, id: `a${seq++}`, tabs, activeTab: 0 };
    return {
      id,
      area,
      rect: { x: 40 * i, y: 40 * i, w: 640, h: 480 },
      mode: 'normal',
      z: i + 1,
    };
  });
  return {
    ...createEmptyFrame(),
    windows,
    windowViewport: { w: 1280, h: 800 },
    paneSeq: seq,
    ...base,
  };
}

/** Load `frame` as the active desktop. */
export function loadFrame(frame: FrameState, workspaceId = 'desktop'): FrameState {
  layoutStore.dispatch({ type: 'LOAD_WORKSPACE', workspaceId, frame });
  return layoutStore.getSnapshot().frame;
}

/** The live frame. */
export function current(): FrameState {
  return layoutStore.getSnapshot().frame;
}
