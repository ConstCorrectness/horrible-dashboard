/**
 * The top workspace-tab strip (Blender-style): one tab per frame preset (always
 * shown, lazily created on first click) plus any custom workspaces, a "+" to
 * create one, and a right-click menu (rename / reset preset / delete custom).
 * Replaces the old rail-as-switcher.
 */
import { useSyncExternalStore } from 'react';
import {
  addContextMenuProvider,
  dialogs,
  framePersistence,
  hasCapability,
  layoutStore,
  openContextMenu,
  registry,
  toastsStore,
  useWorkspaces,
  windowControl,
  type ContextMenuItem,
} from '@horrible/core';

import { useHorizontalWheel } from '../hooks/useHorizontalWheel';
import { useAppFullscreen } from '../hooks/useAppFullscreen';
import { WindowControls } from './WindowChrome';

// The strip renders from AppShell before the Frame ever mounts (home view), so
// it needs the frame styles itself; Vite dedupes the double import.
import './frame.css';

/**
 * The workspace tab menu. Registered on the shared registry rather than rendered
 * inline: the strip now only says *which workspace* was right-clicked, and this
 * decides what can be done to it.
 *
 * `isPreset` is the whole contextual difference — a preset workspace can be reset
 * to its declaration but never deleted (its tab would come straight back from the
 * manifest), and a custom one is the reverse. Showing both and disabling one would
 * be a lie in both directions.
 */
addContextMenuProvider({
  kind: 'workspace.tab',
  items: (target) => {
    const id = String(target.workspaceId ?? '');
    const name = String(target.workspaceName ?? id);
    const items: ContextMenuItem[] = [];
    if (hasCapability('window.perWorkspace')) {
      items.push({
        id: 'workspace.openInWindow',
        label: 'Open in new window',
        run: () => void windowControl()?.openWorkspaceWindow(id),
      });
    }
    items.push({
      id: 'workspace.rename',
      label: 'Rename',
      run: () => void renameWorkspace(id, name),
    });
    items.push(
      target.isPreset
        ? { id: 'workspace.reset', label: 'Reset to preset', run: () => void resetWorkspace(id) }
        : {
            id: 'workspace.delete',
            label: 'Delete',
            danger: true,
            run: () => void removeWorkspace(id, name),
          },
    );
    return items;
  },
});

async function renameWorkspace(id: string, currentName: string): Promise<void> {
  const name = await dialogs.prompt({
    title: 'Rename workspace',
    defaultValue: currentName,
    confirmLabel: 'Rename',
  });
  if (name?.trim()) await framePersistence.renameWorkspace(id, name.trim());
}

async function removeWorkspace(id: string, name: string): Promise<void> {
  const ok = await dialogs.confirm({
    title: 'Delete workspace',
    message: `“${name}” and its layout will be removed. This can't be undone.`,
    confirmLabel: 'Delete',
    danger: true,
  });
  if (ok) {
    await framePersistence.removeWorkspace(id);
    toastsStore.add('info', 'Workspace deleted', `“${name}” was removed.`);
  }
}

async function resetWorkspace(id: string): Promise<void> {
  await framePersistence.switchWorkspace(id);
  await framePersistence.resetLayout();
}

export function WorkspaceTabs() {
  const { workspaces, activeId } = useWorkspaces();
  // When a native shell grants `chrome.workspaceTabs`, this strip IS the
  // (undecorated) window's titlebar: `data-tauri-drag-region` on the empty strip
  // space moves the window and maximizes on double-click (handled natively by
  // the webview), and it hosts the min/max/close controls. Interactive children
  // (tabs, buttons) aren't drag regions, so their clicks still land.
  const nativeChrome = hasCapability('chrome.workspaceTabs');
  // The strip stops being a drag region while the app is fullscreen. The native
  // `data-tauri-drag-region` handler maximizes on double-click *inside the
  // webview*, never reaching `window_toggle_maximize`, and maximizing a
  // fullscreen undecorated window is what paints the taskbar-shaped black band
  // along the bottom (see enter_fullscreen in src-tauri/src/window.rs). There is
  // nothing to drag in fullscreen anyway.
  const { fullscreen } = useAppFullscreen();
  const dragRegion = nativeChrome && !fullscreen;
  // Has to stay reachable once the tabs outgrow the width.
  const wheelRef = useHorizontalWheel<HTMLDivElement>();
  const { frame } = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const mode = frame.mode;

  /**
   * A floating desktop hides the strip: it is a desktop, and a desktop does not
   * have a tab bar. Switching moves to the taskbar's pips and management to the
   * Start menu, so nothing the strip did becomes unreachable.
   *
   * It cannot simply not render, though. When the native shell grants
   * `chrome.workspaceTabs` this strip **is** the undecorated window's titlebar —
   * it carries `data-tauri-drag-region` and the min/max/close controls — so
   * returning null there would leave a window that cannot be moved or closed. A
   * slim drag bar takes its place instead, which is exactly what a detached
   * per-workspace window already does.
   */
  if (mode === 'floating') {
    if (!nativeChrome) return null;
    return (
      <header className="frame-tabs frame-tabs--native frame-tabs--bare">
        <div className="frame-tabs-scroll" {...(dragRegion ? { 'data-tauri-drag-region': '' } : {})} />
        <WindowControls />
      </header>
    );
  }
  const presets = registry.framePresets;
  const presetIds = new Set(presets.map((p) => p.id));
  const entries = [
    ...presets.map((p) => ({ id: p.id, label: p.name, glyph: p.icon ?? p.name[0] })),
    ...workspaces
      .filter((w) => !presetIds.has(w.id))
      .map((w) => ({ id: w.id, label: w.name, glyph: undefined as string | undefined })),
  ];

  return (
    <header
      className={`frame-tabs${nativeChrome ? ' frame-tabs--native' : ''}`}
      role="tablist"
      aria-label="Workspaces"
    >
      {/* The app menu. It used to run `shell.home`, which was a no-op — `home`
          is the desktop now, and the desktop is the only view this button is
          ever visible from, so the corner of the screen every OS reserves for
          its menu did nothing. The items live in the `desktop` module's
          `shell.app` provider; the menu opens at the button's bottom-left so it
          hangs under the logo rather than under the pointer. */}
      <button
        className="frame-tabs-home"
        title="Menu"
        aria-haspopup="menu"
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          openContextMenu({ clientX: r.left, clientY: r.bottom }, { kind: 'shell.app' });
        }}
      >
        <img src="/logo.svg" alt="Menu" />
      </button>
      <div
        className="frame-tabs-scroll"
        ref={wheelRef}
        {...(dragRegion ? { 'data-tauri-drag-region': '' } : {})}
      >
        {entries.map((entry) => (
          <button
            key={entry.id}
            role="tab"
            aria-selected={entry.id === activeId}
            className={`frame-tab${entry.id === activeId ? ' active' : ''}`}
            // Through the registry so the shell enters the workspace view first;
            // the Frame picks the switch up as a pending workspace.
            onClick={() => registry.switchWorkspace(entry.id)}
            onContextMenu={(e) => {
              e.preventDefault();
              openContextMenu(e, {
                kind: 'workspace.tab',
                workspaceId: entry.id,
                workspaceName: entry.label,
                isPreset: presetIds.has(entry.id),
              });
            }}
          >
            {entry.glyph ? <span className="frame-tab-glyph">{entry.glyph}</span> : null}
            <span className="frame-tab-label">{entry.label}</span>
            {/* Which paradigm this desktop runs, on the tab it belongs to. The
                mode is a property of a workspace, but `FrameState` only holds
                the active one's — so only the active tab can honestly show it.
                A span, not a button: this sits inside a button already, and
                nesting one is invalid. The tray's ▦/❐ toggle is the control. */}
            {entry.id === activeId && (
              <span
                className="frame-tab-mode"
                aria-hidden="true"
                title={mode === 'tiling' ? 'Tiling' : 'Floating windows'}
              >
                {mode === 'tiling' ? '▦' : '❐'}
              </span>
            )}
          </button>
        ))}
        <button
          className="frame-tab frame-tab--new"
          title="New workspace"
          onClick={() => void registry.runCommand('workspace.new')}
        >
          ＋
        </button>
      </div>
      {nativeChrome && <WindowControls />}
    </header>
  );
}

/**
 * The titlebar for a **detached** per-workspace OS window (`window.perWorkspace`):
 * the full workspace-switcher strip is stripped down to a slim, draggable bar
 * showing just the workspace name plus the min/maximize/close controls — the
 * window is dedicated to one workspace, so there's nothing to switch. Keeps the
 * frame below (rail + docks + center) intact, Blender detached-area style.
 */
export function DetachedTitlebar() {
  const { workspaces, activeId } = useWorkspaces();
  const nativeChrome = hasCapability('chrome.workspaceTabs');
  // Same rule as the full strip: no drag region while fullscreen — see WorkspaceTabs.
  const { fullscreen } = useAppFullscreen();
  const name = workspaces.find((w) => w.id === activeId)?.name ?? 'Workspace';
  const dragProps = nativeChrome && !fullscreen ? { 'data-tauri-drag-region': '' } : {};
  return (
    <header
      className={`frame-tabs frame-tabs--detached${nativeChrome ? ' frame-tabs--native' : ''}`}
    >
      <div className="frame-tabs-scroll" {...dragProps}>
        <span className="detached-title" {...dragProps}>
          {name}
        </span>
      </div>
      {nativeChrome && <WindowControls />}
    </header>
  );
}
