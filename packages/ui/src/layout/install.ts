/**
 * Installing the frame engine into the shell.
 *
 * This deliberately does **not** live in `Frame.tsx`'s mount effect, where it
 * used to. Everything here is shell-level — the LayoutController the agent's
 * tools drive, the debounced autosave, the `frame` module's commands and
 * keybindings, and hydrating the active workspace — and none of it has anything
 * to do with the tiling frame being on screen.
 *
 * Tying it to the Frame's mount was survivable while the Frame was the only
 * place you could be. It stopped being once the desktop became the landing
 * surface: on a fresh boot the Frame is not mounted, so nothing was installed —
 * no layout agent tools, no region or backdrop commands, and no hydrated
 * workspace — until the user happened to visit the workspace view. The failure
 * is silent in every one of those cases.
 */
import {
  executeTool,
  findArea,
  focusAreaDirection,
  framePersistence,
  fullscreenFocusedArea,
  installFrameController,
  joinAreaDirection,
  layoutStore,
  minibuffer,
  movePaneDirection,
  openFramePane,
  registry,
  resetRailPrefs,
  splitAreaBy,
  toggleDock,
  toggleRegion,
  workspaceStore,
  type OpenPaneOptions,
} from '@horrible/core';

let installed = false;

/** Idempotent, and safe to call before anything renders. */
export function installFrameShell(): void {
  if (installed) return;
  installed = true;
  installFrameController();
  framePersistence.bindAutosave();
  if (import.meta.env.DEV) {
    // Dev/E2E handle (the frame-engine analogue of __horribleWorkspace).
    // `exec` runs a relayed tool by name — the same entry point agent/REPL
    // calls hit — so layout verbs can be exercised from the console.
    (
      window as Window & {
        __horribleFrame?: {
          store: typeof layoutStore;
          registry: typeof registry;
          exec: typeof executeTool;
        };
      }
    ).__horribleFrame = { store: layoutStore, registry, exec: executeTool };
  }
  // Frame-owned commands (the palette/keybinding surface for shell chrome).
  // Directional commands act on the FOCUSED area, vim/i3 style.
  const focusedAreaId = () => layoutStore.getSnapshot().frame.focusedAreaId;
  const NAVS = ['left', 'right', 'up', 'down'] as const;
  const SPLIT_FOR: Record<(typeof NAVS)[number], 'left' | 'right' | 'above' | 'below'> = {
    left: 'left',
    right: 'right',
    up: 'above',
    down: 'below',
  };
  const nthWorkspace = (n: number): string | undefined => {
    const presets = registry.framePresets.map((p) => p.id);
    const customs = workspaceStore
      .getSnapshot()
      .workspaces.map((w) => w.id)
      .filter((id) => !presets.includes(id));
    return [...presets, ...customs][n - 1];
  };
  registry.register({
    id: 'frame',
    title: 'Frame',
    commands: [
      {
        id: 'area.fullscreen',
        // Kept under its old id (saved keymap overrides name it) but it is no
        // longer area-only: on a floating desktop it presents the focused
        // window's pane over the whole shell. One key, one meaning — "let this
        // thing fill the screen" — regardless of which paradigm you are in.
        title: 'Toggle fullscreen for the focused pane',
        run: () => fullscreenFocusedArea(),
      },
      ...NAVS.map((dir) => ({
        id: `area.focus:${dir}`,
        title: `Area: Focus ${dir}`,
        run: () => void focusAreaDirection(dir),
      })),
      ...NAVS.map((dir) => ({
        id: `area.split:${dir}`,
        title: `Area: Split ${dir}`,
        run: () => {
          const areaId = focusedAreaId();
          if (areaId) splitAreaBy(areaId, SPLIT_FOR[dir]);
        },
      })),
      ...NAVS.map((dir) => ({
        id: `pane.move:${dir}`,
        title: `Pane: Move ${dir}`,
        run: () => void movePaneDirection(dir),
      })),
      {
        id: 'area.join',
        title: 'Area: Join neighbor',
        run: () => {
          const areaId = focusedAreaId();
          if (!areaId) return;
          for (const dir of NAVS) if (joinAreaDirection(areaId, dir)) break;
        },
      },
      {
        id: 'minibuffer.open',
        title: 'Minibuffer: Run a command (M-x)',
        run: () => minibuffer.open('/'),
      },
      {
        // Also the escape hatch for a fully-hidden rail (its context menu is
        // unreachable once every glyph on it is hidden).
        id: 'rail.reset',
        title: 'Rails: Reset customization',
        run: () => resetRailPrefs(),
      },
      { id: 'dock.toggle:left', title: 'Dock: Toggle left', run: () => void toggleDock('left') },
      {
        id: 'dock.toggle:right',
        title: 'Dock: Toggle right',
        run: () => void toggleDock('right'),
      },
      {
        id: 'dock.toggle:bottom',
        title: 'Dock: Toggle bottom',
        run: () => void toggleDock('bottom'),
      },
      {
        id: 'region.toggle:left',
        title: 'Region: Toggle left',
        run: () => {
          const areaId = focusedAreaId();
          if (!areaId) return;
          const area = findArea(layoutStore.getSnapshot().frame.center, areaId);
          const active = area?.tabs[area.activeTab];
          if (active) void toggleRegion(active.instanceId, 'left');
        },
      },
      {
        id: 'region.toggle:right',
        title: 'Region: Toggle right',
        run: () => {
          const areaId = focusedAreaId();
          if (!areaId) return;
          const area = findArea(layoutStore.getSnapshot().frame.center, areaId);
          const active = area?.tabs[area.activeTab];
          if (active) void toggleRegion(active.instanceId, 'right');
        },
      },
      {
        id: 'region.toggle:bottom',
        title: 'Region: Toggle bottom',
        run: () => {
          const areaId = focusedAreaId();
          if (!areaId) return;
          const area = findArea(layoutStore.getSnapshot().frame.center, areaId);
          const active = area?.tabs[area.activeTab];
          if (active) void toggleRegion(active.instanceId, 'bottom');
        },
      },
      ...Array.from({ length: 9 }, (_, i) => ({
        id: `workspace.switch:${i + 1}`,
        title: `Workspace: Switch to #${i + 1}`,
        run: () => {
          const id = nthWorkspace(i + 1);
          if (id) registry.switchWorkspace(id);
        },
      })),
    ],
    keybindings: [
      // ctrl+space is the IME toggle on Windows and the input-source switch on
      // macOS — neither reaches the page — so those two get an alternative.
      { key: 'ctrl+space', command: 'area.fullscreen', platforms: ['linux'] },
      { key: 'mod+alt+f', command: 'area.fullscreen', platforms: ['mac', 'win'] },
      // `override` so a focused editor pane can't shadow it — the minibuffer
      // is the escape hatch and has to be reachable from anywhere.
      { key: 'alt+x', command: 'minibuffer.open', override: true },
      ...NAVS.map((dir) => ({ key: `alt+${dir}`, command: `area.focus:${dir}` })),
      ...NAVS.map((dir) => ({ key: `alt+shift+${dir}`, command: `pane.move:${dir}` })),
      ...NAVS.map((dir) => ({ key: `mod+alt+${dir}`, command: `area.split:${dir}` })),
      { key: 'mod+alt+j', command: 'area.join' },
      { key: 'mod+b', command: 'dock.toggle:left' },
      { key: 'mod+alt+b', command: 'dock.toggle:right' },
      { key: 'mod+j', command: 'dock.toggle:bottom' },
      { key: 't', command: 'region.toggle:left' },
      { key: 'n', command: 'region.toggle:right' },
      { key: 'b', command: 'region.toggle:bottom' },
      // Workspace switching splits by host. `mod+1..9` is browser tab switching
      // and is NOT cancellable, so in a tab these bindings have never once
      // fired; the browser build gets alt+N, which nothing claims.
      ...Array.from({ length: 9 }, (_, i) => ({
        key: `mod+${i + 1}`,
        command: `workspace.switch:${i + 1}`,
        hosts: ['desktop' as const],
      })),
      ...Array.from({ length: 9 }, (_, i) => ({
        key: `alt+${i + 1}`,
        command: `workspace.switch:${i + 1}`,
        hosts: ['browser' as const],
      })),
    ],
  });
  void framePersistence.hydrate().finally(() => {
    hydrated = true;
    const queued = pending.splice(0);
    for (const run of queued) run();
  });
}

/**
 * Work requested before the active workspace finished loading.
 *
 * A boot-time `registry.openPanel` — a deep link, a detached window's initial
 * workspace, a plugin opening its pane on registration — arrives while the frame
 * is still the empty seed. Running it then puts the pane into a frame that
 * `LOAD_WORKSPACE` is about to replace wholesale, so it simply vanishes with no
 * error anywhere. This is the same guard the Frame's nonce/replay effect used to
 * provide, moved to where the work is actually started.
 */
let hydrated = false;
const pending: Array<() => void> = [];

function whenHydrated(run: () => void): void {
  if (hydrated) run();
  else pending.push(run);
}

/** `registry.openPanel`'s target. Role-routed, or windowed on a floating desktop. */
export function openPaneWhenReady(viewId: string, opts?: OpenPaneOptions): void {
  whenHydrated(() => openFramePane(viewId, opts));
}

/** `registry.switchWorkspace`'s target — switching desktops. */
export function switchWorkspaceWhenReady(workspaceId: string): void {
  whenHydrated(() => void framePersistence.switchWorkspace(workspaceId));
}
