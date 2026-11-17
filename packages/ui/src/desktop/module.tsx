/**
 * The `desktop` module: the built-in backdrops, the commands that switch them,
 * and the desktop's own right-click menu.
 *
 * Registered from `packages/ui` rather than `packages/core/src/modules` for the
 * same reason the `shell` module is: everything it contributes is a React
 * surface that lives here, and core must not import back out of ui.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import {
  closePaneGuarded,
  currentThemeId,
  cycleGroups,
  findPaneAnywhere,
  focusDirection,
  paneDisplayTitle,
  resolveView,
  splitFocused,
  tearOffPane,
  toggleDock,
  toggleMaximizedGroup,
  getSetting,
  layoutStore,
  registry,
  setBackdrop,
  setSetting,
  taskbarEntries,
  type ContextMenuItem,
  type ContextTarget,
  type DockSide,
  type ModuleManifest,
  type SplitSide,
} from '@horrible/core';

import { BUILTIN_BACKDROPS } from './backdrops';
import { docsPageFor, openDocs } from './help-links';
import {
  homeUnderlayId,
  isHomeCollapsed,
  setHomeCollapsed,
  setHomeUnderlay,
  SPLASH_BACKDROP_ID,
  toggleHomeCollapsed,
} from './backdrops/Splash';

/** The four directions. */
const NAVS = ['left', 'right', 'up', 'down'] as const;
const SPLITS: readonly SplitSide[] = ['left', 'right', 'above', 'below'];
const SPLIT_WORDS: Record<SplitSide, string> = {
  left: 'to the left',
  right: 'to the right',
  above: 'above',
  below: 'below',
};
const DOCKS: readonly DockSide[] = ['left', 'right', 'bottom'];
import { OOBE_COMPLETE_KEY } from './constants';
import { ANIMATIONS_SETTING_KEY } from '../workbench/motion';
import {
  DEFAULT_TASKBAR,
  mergeTaskbarConfig,
  TASKBAR_SETTING_KEY,
  ZONES,
  zoneRank,
} from './Taskbar';
import { windowButtonMenu } from './taskbar/WindowButtons';
import { themeMenuItems } from './taskbar/Tray';

/**
 * The `desktop.backdrop:<id>` switch commands are **not** here: the registry
 * synthesizes one per registered provider, so a plugin's backdrop gets its
 * command without a second list to keep in step. See `registry.ts`.
 */
export const desktopModule: ModuleManifest = {
  id: 'desktop',
  title: 'Desktop',
  backdrops: BUILTIN_BACKDROPS,
  commands: [
    {
      /**
       * The app's documentation. A full Docusaurus site is built from `docs/` and
       * published on every push to `main`; linking it beats writing a second copy
       * inside the app. A group's tab-strip menu links the pane's own page — see
       * `titlebarHelpItems` and `help-links.ts`.
       */
      id: 'shell.help',
      title: 'Help: Open the documentation',
      run: () => openDocs(),
    },
    {
      // The home screen is a backdrop, not a pane, so no group verb reaches it.
      id: 'desktop.toggleHome',
      title: 'Desktop: Minimize / restore the home screen',
      run: () => void toggleHomeCollapsed(),
    },
    /*
     * The two directions, named, beside the toggle.
     *
     * A toggle is the wrong shape for the palette and for the agent: both act
     * without seeing the screen, so "toggle" means "put it in whichever state it
     * is not in", and `desktop.showHome` run twice is the home screen hidden.
     * The toggle stays for the keyboard and the menu, where the current state is
     * visible.
     */
    {
      id: 'desktop.hideHome',
      title: 'Desktop: Minimize the home screen — wallpaper, taskbar and the ask bar',
      run: () => void setHomeCollapsed(true),
    },
    {
      id: 'desktop.showHome',
      title: 'Desktop: Show the home screen',
      run: () => void setHomeCollapsed(false),
    },
    {
      /**
       * Bring the home screen up, but only on a desktop that is showing it.
       * `desktop.showHome` *puts* the home screen in the backdrop slot, which on a
       * desktop with a wallpaper would replace that wallpaper; this is the safe
       * verb for the app menu and the Start menu footer.
       */
      id: 'desktop.home',
      title: 'Desktop: Go to the desktop home',
      run: () => {
        if (layoutStore.getSnapshot().frame.backdrop.id === SPLASH_BACKDROP_ID) {
          setHomeCollapsed(false);
        }
      },
    },
    // --- the workbench ------------------------------------------------------
    // Ids keep their `window.` prefix where the verb survived the move from
    // floating windows to docked groups: saved keymap overrides and the agent's
    // `run_command` name them.
    { id: 'window.next', title: 'Group: Focus the next group', run: () => void cycleGroups(1) },
    { id: 'window.prev', title: 'Group: Focus the previous group', run: () => void cycleGroups(-1) },
    {
      id: 'window.maximize',
      title: 'Group: Maximize / restore the focused group',
      run: () => void toggleMaximizedGroup(),
    },
    {
      id: 'window.tearOff',
      title: 'Group: Move the focused tab into a group of its own',
      run: () => {
        const id = layoutStore.getSnapshot().frame.focusedInstanceId;
        if (id) tearOffPane(id);
      },
    },
    ...NAVS.map((dir) => ({
      id: `window.focus:${dir}`,
      title: `Group: Focus ${dir}`,
      run: () => void focusDirection(dir),
    })),
    ...SPLITS.map((side) => ({
      id: `workbench.split:${side}`,
      title: `Group: Move the focused tab to a new group ${SPLIT_WORDS[side]}`,
      run: () => void splitFocused(side),
    })),
    ...DOCKS.map((side) => ({
      id: `workbench.toggleDock:${side}`,
      title: `Dock: Show / hide the ${side} dock`,
      run: () => void toggleDock(side),
    })),
  ],
  keybindings: [
    // Every chord here was checked against `keymap/reserved.ts`. The conventional
    // ones are unavailable: `alt+tab` never reaches the page on Windows or Linux,
    // `meta+space`/`ctrl+space` are Spotlight and the IME toggle. A binding that
    // silently never fires is worse than a less familiar one that does.
    { key: 'alt+grave', command: 'window.next' },
    { key: 'alt+shift+grave', command: 'window.prev' },
    { key: 'mod+alt+up', command: 'window.maximize' },
    // Where the floating desktop snapped halves, the workbench splits: the same
    // keys put the focused tab on that side of its group.
    { key: 'mod+shift+left', command: 'workbench.split:left' },
    { key: 'mod+shift+right', command: 'workbench.split:right' },
    { key: 'mod+shift+up', command: 'workbench.split:above' },
    { key: 'mod+shift+down', command: 'workbench.split:below' },
    // Docks. Not `t`/`n`/`b` — those are a pane's own region strips — and not
    // `mod+b`/`mod+j`, which panes already bind.
    { key: 'mod+alt+[', command: 'workbench.toggleDock:left' },
    { key: 'mod+alt+]', command: 'workbench.toggleDock:right' },
    { key: 'mod+alt+j', command: 'workbench.toggleDock:bottom' },
    ...NAVS.map((dir) => ({ key: `alt+${dir}`, command: `window.focus:${dir}` })),
  ],
  settings: [
    {
      key: TASKBAR_SETTING_KEY,
      title: 'Taskbar',
      description:
        'Which zones the taskbar shows and in what order, as JSON. Zones: start, windows, spacer, mx, tray, clock, agent. Also `position` (bottom/top), `showLabels`, `autoHide`.',
      type: 'string',
      default: JSON.stringify(DEFAULT_TASKBAR),
    },
    {
      key: ANIMATIONS_SETTING_KEY,
      title: 'Animate the workbench',
      description:
        'A newly opened pane slides in, and a pane fades in when its tab is picked. Off makes every change instant. Also off whenever the system asks for reduced motion.',
      type: 'boolean',
      default: true,
    },
    {
      key: OOBE_COMPLETE_KEY,
      title: 'First-run setup has been completed',
      description:
        'Turn this off to see the first-run setup flow again the next time the app starts.',
      type: 'boolean',
      default: false,
    },
  ],
  contextMenu: [
    { kind: 'desktop', items: () => desktopMenuItems() },
    { kind: 'taskbar.window', items: (target) => taskbarWindowMenu(target) },
    { kind: 'taskbar', items: () => taskbarMenu() },
    { kind: 'taskbar.theme', items: () => themeMenuItems(currentThemeId()) },
    // Two providers for one target so the menu draws a separator between the
    // help and the window verbs — the separator only falls between groups.
    { kind: 'window.titlebar', items: (target) => titlebarHelpItems(target) },
    { kind: 'window.titlebar', items: (target) => titlebarWindowItems(target) },
  ],
};

/** The pane a tab-strip right-click was about, if it is still open. */
function titlebarPane(target: ContextTarget) {
  const instanceId = typeof target.instanceId === 'string' ? target.instanceId : null;
  const found = instanceId ? findPaneAnywhere(layoutStore.getSnapshot().frame, instanceId) : null;
  return found ? { pane: found.pane, location: found.location } : null;
}

/**
 * Help for the pane under the pointer: its module's page in the docs, then the
 * docs as a whole. The per-pane item is dropped, not greyed, for a module with
 * no page — `docsPageFor` never guesses a URL.
 */
function titlebarHelpItems(target: ContextTarget): ContextMenuItem[] {
  const hit = titlebarPane(target);
  if (!hit) return [];
  const moduleId = registry.viewOwnerId(hit.pane.viewId);
  const page = docsPageFor(moduleId);
  const title = resolveView(hit.pane.viewId)?.title ?? paneDisplayTitle(hit.pane);
  return [
    ...(page
      ? [
          {
            id: 'window.help',
            label: `Help: ${title}`,
            detail: `What the ${registry.viewOwner(hit.pane.viewId) ?? title} pane does and how to use it`,
            run: () => openDocs(page),
          },
        ]
      : []),
    { id: 'window.helpAll', label: 'All documentation', run: () => openDocs() },
  ];
}

/** The group verbs, named by the group's current state. */
function titlebarWindowItems(target: ContextTarget): ContextMenuItem[] {
  const hit = titlebarPane(target);
  if (!hit) return [];
  const { pane, location } = hit;
  const frame = layoutStore.getSnapshot().frame;
  const maximized = frame.maximizedAreaId === location.areaId;
  const dock = location.kind === 'dock' ? location.side : null;
  return [
    ...(dock
      ? [{ id: 'window.hideDock', label: `Hide the ${dock} dock`, run: () => void toggleDock(dock, false) }]
      : []),
    {
      id: 'window.maximize',
      label: maximized ? 'Restore' : 'Maximize',
      run: () => void toggleMaximizedGroup(location.areaId),
    },
    {
      id: 'window.tearOff',
      label: location.kind === 'center' ? 'Split into its own group' : 'Move to the centre',
      run: () => void tearOffPane(pane.instanceId),
    },
    {
      id: 'window.close',
      label: 'Close',
      danger: true,
      run: () => void closePaneGuarded(pane.instanceId),
    },
  ];
}

/**
 * The backdrop picker, resolved from the registry at click time so a plugin's
 * backdrop appears without the menu knowing plugins exist.
 */
function backdropMenuItems(): ContextMenuItem[] {
  const { frame } = layoutStore.getSnapshot();
  return registry.backdrops.map((b) => ({
    id: `desktop.backdrop:${b.id}`,
    label: b.title,
    // `detail`, not `hint`, for the same reason the theme picker uses it: a
    // description is prose and takes the row away from the name.
    detail: b.description,
    checked: frame.backdrop.id === b.id,
    run: () => void setBackdrop({ id: b.id }),
  }));
}

/**
 * The wallpaper painted behind the home screen.
 *
 * Built from the same registry list as `backdropMenuItems`, minus two exclusions
 * that would each fail badly rather than visibly — the home surface itself
 * (`splash` inside `splash` is an infinite mount) and any other `interactive`
 * provider (two surfaces wanting the pointer in one square of screen, the lower
 * one silently dead). `resolveHomeUnderlay` enforces the same rule at render
 * time; this filter is so the menu never offers what the renderer would refuse.
 */
function homeUnderlayMenuItems(): ContextMenuItem[] {
  const active = homeUnderlayId();
  return registry.backdrops
    .filter((b) => b.id !== SPLASH_BACKDROP_ID && !b.interactive)
    .map((b) => ({
      id: `desktop.homeUnderlay:${b.id}`,
      label: b.title,
      detail: b.description,
      checked: active === b.id,
      run: () => void setHomeUnderlay(b.id),
    }));
}

/**
 * Customizing the taskbar, from a right-click on the strip.
 *
 * The config was already a setting (`desktop.taskbar`) and already merged over
 * the defaults — what it had no way of being was *edited*, short of typing JSON
 * into the settings page. Everything here writes that same setting through the
 * same merge, so the menu and a hand-written value cannot mean different things.
 *
 * Zones are toggles rather than a reorderable list: a menu is a poor reordering
 * surface, and the order that matters (start … spacer … tray, clock) is the one
 * people keep. Toggling a zone off removes it and toggling it back inserts it at
 * its **default position**, so the strip cannot end up in an order the user
 * never asked for just because they turned the clock off and on again.
 */
function taskbarMenu(): ContextMenuItem[] {
  const config = mergeTaskbarConfig(getSetting<string>(TASKBAR_SETTING_KEY));
  const write = (patch: Partial<typeof config>) =>
    void setSetting(TASKBAR_SETTING_KEY, JSON.stringify({ ...config, ...patch }));

  const toggleZone = (zone: string) => {
    if (config.zones.includes(zone)) {
      write({ zones: config.zones.filter((z) => z !== zone) });
      return;
    }
    // Re-inserted at its canonical position, not appended: a zone switched off
    // and on again should land back where it belongs, not at the far end of the
    // strip past the clock.
    const next = [...config.zones];
    const at = next.findIndex((z) => zoneRank(z) > zoneRank(zone));
    next.splice(at < 0 ? next.length : at, 0, zone);
    write({ zones: next });
  };

  return [
    {
      id: 'taskbar.zones',
      label: 'Show',
      run: () => {},
      submenu: Object.entries(ZONES).map(([id, zone]) => ({
        id: `taskbar.zone:${id}`,
        label: zone.title,
        checked: config.zones.includes(id),
        run: () => toggleZone(id),
      })),
    },
    {
      id: 'taskbar.position',
      label: 'Position',
      run: () => {},
      submenu: (['bottom', 'top'] as const).map((position) => ({
        id: `taskbar.position:${position}`,
        label: position === 'bottom' ? 'Bottom' : 'Top',
        checked: config.position === position,
        run: () => write({ position }),
      })),
    },
    {
      id: 'taskbar.labels',
      label: 'Show labels',
      checked: config.showLabels,
      run: () => write({ showLabels: !config.showLabels }),
    },
    {
      id: 'taskbar.autohide',
      label: 'Auto-hide',
      detail: 'Slides away until the pointer reaches the edge.',
      checked: config.autoHide,
      run: () => write({ autoHide: !config.autoHide }),
    },
    {
      id: 'taskbar.reset',
      label: 'Reset the taskbar',
      // The escape hatch, and the reason zones can be hidden freely: turning off
      // every zone leaves a bare strip that still right-clicks, so there is no
      // way to customize yourself out of reach of this item.
      run: () => void setSetting(TASKBAR_SETTING_KEY, JSON.stringify(DEFAULT_TASKBAR)),
    },
  ];
}

/** Items for one taskbar button, resolved from that pane's live entry. */
function taskbarWindowMenu(target: ContextTarget): ContextMenuItem[] {
  const instanceId = typeof target.instanceId === 'string' ? target.instanceId : null;
  if (!instanceId) return [];
  const entry = taskbarEntries(layoutStore.getSnapshot().frame).find(
    (e) => e.instanceId === instanceId,
  );
  // The pane closed between the right-click and the menu resolving. Return no
  // items so the menu simply does not open, rather than offering verbs against
  // something that is gone.
  return entry ? windowButtonMenu(entry) : [];
}

function desktopMenuItems(): ContextMenuItem[] {
  const { frame } = layoutStore.getSnapshot();
  return [
    { id: 'desktop.backdrop', label: 'Backdrop', run: () => {}, submenu: backdropMenuItems() },
    // Only on a desktop actually showing the home screen. On any other backdrop
    // there is nothing to minimize, and an item that silently switches you to
    // the home screen in order to collapse it would be a trap.
    ...(frame.backdrop.id === SPLASH_BACKDROP_ID
      ? [
          {
            id: 'desktop.toggleHome',
            label: isHomeCollapsed() ? 'Show the home screen' : 'Minimize the home screen',
            detail: isHomeCollapsed()
              ? 'Brings the greeting and connectors back'
              : 'Leaves the wallpaper, the taskbar and the ask bar',
            run: () => void toggleHomeCollapsed(),
          },
          // The wallpaper *behind* the home screen, which is a different question
          // from `Backdrop` above — that one chooses what occupies the slot, and
          // choosing anything there takes the home screen away entirely. Listed
          // second so the two read in that order: what this desktop is, then what
          // it looks like underneath.
          {
            id: 'desktop.homeUnderlay',
            label: 'Wallpaper behind home',
            run: () => {},
            submenu: homeUnderlayMenuItems(),
          },
        ]
      : []),
  ];
}

/** Idempotent, like every other `registry.register` call. */
export function registerDesktopModule(): void {
  registry.register(desktopModule);
}
