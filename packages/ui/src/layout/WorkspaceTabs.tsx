/**
 * The top workspace-tab strip (Blender-style): one tab per frame preset (always
 * shown, lazily created on first click) plus any custom workspaces, a "+" to
 * create one, and a right-click menu (rename / reset preset / delete custom).
 * Replaces the old rail-as-switcher.
 */
import {
  addContextMenuProvider,
  dialogs,
  framePersistence,
  hasCapability,
  openContextMenu,
  registry,
  toastsStore,
  useWorkspaces,
  windowControl,
  type ContextMenuItem,
} from '@horrible/core';

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
    items.push({ id: 'workspace.rename', label: 'Rename', run: () => void renameWorkspace(id, name) });
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
      <button
        className="frame-tabs-home"
        title="Home"
        onClick={() => void registry.runCommand('shell.home')}
      >
        <img src="/logo.svg" alt="Home" />
      </button>
      <div
        className="frame-tabs-scroll"
        {...(nativeChrome ? { 'data-tauri-drag-region': '' } : {})}
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
  const name = workspaces.find((w) => w.id === activeId)?.name ?? 'Workspace';
  const dragProps = nativeChrome ? { 'data-tauri-drag-region': '' } : {};
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
