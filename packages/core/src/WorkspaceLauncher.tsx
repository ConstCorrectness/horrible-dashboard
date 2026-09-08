import { staggerIndex } from './DataList';
import { registry } from './registry';
import { useWorkspaces } from './workspace-store';

import './workspace-launcher.css';

/**
 * The ways of working, offered on the front door.
 *
 * The app ships 17 hand-designed workspace presets — AI Research, Training, Lab,
 * Evals, Interpretability, Data Ops — each seeding a complete arrangement for one
 * kind of work. Until now they were surfaced in exactly one place: the top tab
 * strip. And `WorkspaceTabs` returns `null` for a floating desktop, which is what
 * `DEFAULT_BOOT_WORKSPACE` is. So a clean install opened on the one mode where the
 * strip does not render, and the Start menu's Desktops group lists *created*
 * workspaces — of which a clean install has one. The presets were unreachable
 * without knowing to switch desktop modes first.
 *
 * This row is the fix, and it is deliberately here rather than in the taskbar: the
 * home surface is where someone is already looking for a next action, and a preset
 * is a *decision about what to do*, not a window-management control.
 *
 * It reads `registry.framePresets` directly, the same source the tab strip reads, so
 * a module that contributes a frame appears here for free and the two can never
 * drift. It lives in core rather than beside the other home components in
 * `packages/ui` because the dashboard's welcome widget — a core module — renders it
 * too, and a core module importing from ui is the cycle that put `Avatar3D` here. The active workspace is filtered out — this surface renders on the desktop
 * you are already standing in, and offering it is offering nowhere.
 */
export function WorkspaceLauncher() {
  const { activeId } = useWorkspaces();
  const presets = registry.framePresets.filter((p) => p.id !== activeId);
  if (presets.length === 0) return null;

  return (
    <section className="ws-launcher" aria-label="Workspaces">
      <h2 className="ws-launcher-head">Ways to work</h2>
      <div className="ws-launcher-grid">
        {presets.map((preset, i) => (
          <button
            key={preset.id}
            type="button"
            className="ws-tile"
            // Capped by `staggerIndex` for the reason `DataList` caps it: the delay
            // is linear in the count, and there are 17 of these.
            style={{ animationDelay: `calc(var(--stagger-step) * ${staggerIndex(i)})` }}
            onClick={() => registry.switchWorkspace(preset.id)}
          >
            {/* The workspace glyph, the same one the tab strip and the Start menu
                draw. This is the manifest-icon convention, not iconography inside a
                pane — a preset's identity is that character. */}
            <span className="ws-tile-glyph" aria-hidden="true">
              {preset.icon ?? preset.name[0]}
            </span>
            <span className="ws-tile-name">{preset.name}</span>
            {preset.description && <span className="ws-tile-desc">{preset.description}</span>}
          </button>
        ))}
      </div>
    </section>
  );
}
