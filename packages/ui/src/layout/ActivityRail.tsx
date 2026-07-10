/**
 * The VS Code-style activity rail: home/logo on top, then one glyph per
 * registered tool view. Clicking a tool opens it in its dock (or hides the dock
 * when it is already the visible tool — the VS Code toggle). Palette at the
 * bottom. The workspace switcher lives in the top tab strip, not here.
 */
import {
  openToolInDock,
  registry,
  roleOf,
  toggleDock,
  type DockSide,
  type FrameState,
} from '@horrible/core';

export function ActivityRail({ frame }: { frame: FrameState }) {
  const tools = [...registry.panels, ...registry.widgets].filter((v) => roleOf(v.id) === 'tool');

  const stateOf = (viewId: string): { side: DockSide; active: boolean } | null => {
    for (const side of ['left', 'right', 'bottom'] as const) {
      const dock = frame.docks[side];
      const tool = dock.tools.find((t) => t.viewId === viewId);
      if (tool) {
        return { side, active: dock.visible && dock.activeTool === tool.instanceId };
      }
    }
    return null;
  };

  return (
    <nav className="frame-rail" aria-label="Tools">
      {tools.map((tool) => {
        const state = stateOf(tool.id);
        return (
          <button
            key={tool.id}
            className={`frame-rail-btn${state?.active ? ' active' : ''}`}
            title={tool.title}
            onClick={() => {
              if (state?.active) toggleDock(state.side, false);
              else openToolInDock(tool.id);
            }}
          >
            {tool.icon ?? tool.title[0]}
          </button>
        );
      })}
      <div className="frame-rail-spacer" />
      <button
        className="frame-rail-btn"
        title="Commands (Ctrl+K)"
        onClick={() => void registry.runCommand('shell.commandPalette')}
      >
        ⌘
      </button>
    </nav>
  );
}
