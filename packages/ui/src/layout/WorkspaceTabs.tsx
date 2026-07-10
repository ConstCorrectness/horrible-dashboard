/**
 * The top workspace-tab strip (Blender-style): one tab per frame preset (always
 * shown, lazily created on first click) plus any custom workspaces, a "+" to
 * create one, and a right-click menu (rename / reset preset / delete custom).
 * Replaces the old rail-as-switcher.
 */
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { dialogs, framePersistence, registry, toastsStore, useWorkspaces } from '@horrible/core';

// The strip renders from AppShell before the Frame ever mounts (home view), so
// it needs the frame styles itself; Vite dedupes the double import.
import './frame.css';

interface Menu {
  x: number;
  y: number;
  workspaceId: string;
  workspaceName: string;
  isPreset: boolean;
}

export function WorkspaceTabs() {
  const { workspaces, activeId } = useWorkspaces();
  const [menu, setMenu] = useState<Menu | null>(null);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener('mousedown', close);
    window.addEventListener('keydown', close);
    return () => {
      window.removeEventListener('mousedown', close);
      window.removeEventListener('keydown', close);
    };
  }, [menu]);

  const presets = registry.framePresets;
  const presetIds = new Set(presets.map((p) => p.id));
  const entries = [
    ...presets.map((p) => ({ id: p.id, label: p.name, glyph: p.icon ?? p.name[0] })),
    ...workspaces
      .filter((w) => !presetIds.has(w.id))
      .map((w) => ({ id: w.id, label: w.name, glyph: undefined as string | undefined })),
  ];

  const rename = async (id: string, currentName: string) => {
    setMenu(null);
    const name = await dialogs.prompt({
      title: 'Rename workspace',
      defaultValue: currentName,
      confirmLabel: 'Rename',
    });
    if (name?.trim()) await framePersistence.renameWorkspace(id, name.trim());
  };

  const remove = async (id: string, name: string) => {
    setMenu(null);
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
  };

  const reset = async (id: string) => {
    setMenu(null);
    await framePersistence.switchWorkspace(id);
    await framePersistence.resetLayout();
  };

  return (
    <header className="frame-tabs" role="tablist" aria-label="Workspaces">
      <button
        className="frame-tabs-home"
        title="Home"
        onClick={() => void registry.runCommand('shell.home')}
      >
        <img src="/logo.svg" alt="Home" />
      </button>
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
            setMenu({
              x: e.clientX,
              y: e.clientY,
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
      {menu &&
        createPortal(
          <div
            className="frame-menu frame-menu--context"
            style={{ left: menu.x, top: menu.y }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <button
              className="frame-menu-item"
              onClick={() => void rename(menu.workspaceId, menu.workspaceName)}
            >
              Rename
            </button>
            {menu.isPreset ? (
              <button className="frame-menu-item" onClick={() => void reset(menu.workspaceId)}>
                Reset to preset
              </button>
            ) : (
              <button
                className="frame-menu-item frame-menu-item--danger"
                onClick={() => void remove(menu.workspaceId, menu.workspaceName)}
              >
                Delete
              </button>
            )}
          </div>,
          document.body,
        )}
    </header>
  );
}
