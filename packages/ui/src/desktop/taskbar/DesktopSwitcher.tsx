/**
 * The desktop switcher: one pip per workspace, because a desktop IS a workspace.
 *
 * **Off by default.** This was originally argued as a compact edge-of-screen
 * switcher complementing the top strip, but in practice the two rendered the
 * same list and ran the same verb, and only one of them could also rename or
 * delete a workspace — so the pair read as one feature drawn twice. The strip is
 * now the switcher (see `DEFAULT_TASKBAR`), and this zone remains for anyone who
 * wants the OS-conventional placement: add `"desktops"` to the `desktop.taskbar`
 * setting's `zones`.
 */
import { registry, useWorkspaces } from '@horrible/core';

import { useHorizontalWheel } from '../../hooks/useHorizontalWheel';

export function DesktopSwitcher({ showLabels }: { showLabels: boolean }) {
  const { workspaces, activeId } = useWorkspaces();
  const wheelRef = useHorizontalWheel<HTMLDivElement>();
  if (workspaces.length < 2) return null;
  return (
    <div className="os-taskbar-desktops" ref={wheelRef} role="group" aria-label="Desktops">
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
