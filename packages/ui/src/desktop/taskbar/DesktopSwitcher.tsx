/**
 * The desktop switcher: one pip per workspace, because a desktop IS a workspace.
 *
 * This is deliberately not a duplicate of the top strip. The strip names them
 * and lets you rename and delete; this is a compact switcher at the edge of the
 * screen, which is where an OS puts one.
 */
import { registry, useWorkspaces } from '@horrible/core';

export function DesktopSwitcher({ showLabels }: { showLabels: boolean }) {
  const { workspaces, activeId } = useWorkspaces();
  if (workspaces.length < 2) return null;
  return (
    <div className="os-taskbar-desktops" role="group" aria-label="Desktops">
      {workspaces.map((w, i) => (
        <button
          key={w.id}
          type="button"
          className={`os-taskbar-desktop${w.id === activeId ? ' is-active' : ''}`}
          aria-label={w.name}
          aria-pressed={w.id === activeId}
          title={w.name}
          onClick={() => registry.switchWorkspace(w.id)}
        >
          {showLabels ? w.name : i + 1}
        </button>
      ))}
    </div>
  );
}
