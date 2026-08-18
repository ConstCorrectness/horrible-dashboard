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
  arrangeDesktop,
  currentThemeId,
  cycleWindows,
  focusWindowDirection,
  setPaneWindowed,
  snapWindow,
  toggleWindowMaximized,
  toggleWindowMinimized,
  getSetting,
  layoutStore,
  registry,
  BOOT_WORKSPACE_KEY,
  DEFAULT_BOOT_WORKSPACE,
  setBackdrop,
  setDesktopMode,
  setSetting,
  taskbarEntries,
  toggleDesktopMode,
  type ContextMenuItem,
  type ContextTarget,
  type ModuleManifest,
} from '@horrible/core';

import { BUILTIN_BACKDROPS } from './backdrops';

/** The four directions, in the order the frame's own bindings use. */
const NAVS = ['left', 'right', 'up', 'down'] as const;
import { OOBE_COMPLETE_KEY } from './constants';
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
    // Converting a desktop in place is **lossy** — `explodeToWindows` can only
    // express split ratios as the rects they happened to occupy and `tileWindows`
    // cannot recover the ratios you dragged, so a round trip quietly degrades the
    // arrangement. The paradigm is a property of the workspace, chosen when you
    // make one (Start ▸ New tiled / New floating); these stay for the cases where
    // converting is genuinely what you want, but they say so in their titles and
    // live in the palette rather than under an ambient one-click control.
    {
      id: 'desktop.toggleMode',
      title: 'Desktop: Convert this desktop (tiling ⇄ floating) — sizes are not preserved',
      run: () => void toggleDesktopMode(),
    },
    {
      id: 'desktop.tiling',
      title: 'Desktop: Convert this desktop to tiling — sizes are not preserved',
      run: () => void setDesktopMode('tiling'),
    },
    {
      id: 'desktop.floating',
      title: 'Desktop: Convert this desktop to floating windows — sizes are not preserved',
      run: () => void setDesktopMode('floating'),
    },
    {
      id: 'desktop.cascade',
      title: 'Desktop: Cascade windows',
      run: () => void arrangeDesktop('cascade'),
    },
    {
      id: 'desktop.grid',
      title: 'Desktop: Tile windows in a grid',
      run: () => void arrangeDesktop('grid'),
    },
    {
      id: 'desktop.columns',
      title: 'Desktop: Tile windows in columns',
      run: () => void arrangeDesktop('columns'),
    },
    {
      id: 'desktop.rows',
      title: 'Desktop: Tile windows in rows',
      run: () => void arrangeDesktop('rows'),
    },
    // --- window management ---------------------------------------------------
    {
      id: 'window.next',
      title: 'Window: Next',
      run: () => void cycleWindows(1),
    },
    { id: 'window.prev', title: 'Window: Previous', run: () => void cycleWindows(-1) },
    {
      id: 'window.minimize',
      title: 'Window: Minimize / restore',
      run: () => void toggleWindowMinimized(),
    },
    {
      id: 'window.maximize',
      title: 'Window: Maximize / restore',
      run: () => void toggleWindowMaximized(),
    },
    {
      id: 'window.float',
      title: 'Window: Open the focused pane in a window',
      run: () => {
        const id = layoutStore.getSnapshot().frame.focusedInstanceId;
        if (id) setPaneWindowed(id, true);
      },
    },
    ...NAVS.map((dir) => ({
      id: `window.focus:${dir}`,
      title: `Window: Focus ${dir}`,
      run: () => void focusWindowDirection(dir),
    })),
    ...(
      [
        ['left', 'left half'],
        ['right', 'right half'],
        ['top', 'maximized'],
        ['bottom', 'bottom half'],
      ] as const
    ).map(([zone, label]) => ({
      id: `window.snap:${zone}`,
      title: `Window: Snap ${label}`,
      run: () => void snapWindow(undefined, zone),
    })),
  ],
  keybindings: [
    // Every chord here was checked against `keymap/reserved.ts`. The conventional
    // ones are unavailable: `alt+tab` never reaches the page on Windows or Linux,
    // `meta+m` is macOS minimize, `meta+space`/`ctrl+space` are Spotlight and the
    // IME toggle. A binding that silently never fires is worse than a less
    // familiar one that does.
    { key: 'alt+grave', command: 'window.next' },
    { key: 'alt+shift+grave', command: 'window.prev' },
    { key: 'mod+alt+down', command: 'window.minimize' },
    { key: 'mod+alt+up', command: 'window.maximize' },
    // No chord for `desktop.toggleMode`. It rearranges every pane on the desktop
    // and cannot be undone exactly, and a lossy whole-desktop rewrite is not
    // something a mistyped chord should be able to do. It is a palette verb.
    // Snapping is `mod+shift+arrow` rather than `mod+alt+arrow`, which the frame
    // already uses to split an area — the two would collide on a tiling desktop,
    // where both are live.
    { key: 'mod+shift+left', command: 'window.snap:left' },
    { key: 'mod+shift+right', command: 'window.snap:right' },
    { key: 'mod+shift+up', command: 'window.snap:top' },
    { key: 'mod+shift+down', command: 'window.snap:bottom' },
    // Directional window focus only while a window has focus, so on a tiling
    // desktop the identical frame bindings (`alt+arrow` = focus area) keep
    // working and these do not shadow them.
    ...NAVS.map((dir) => ({
      key: `alt+${dir}`,
      command: `window.focus:${dir}`,
      when: 'windowFocused',
      priority: 1,
    })),
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
      key: BOOT_WORKSPACE_KEY,
      title: 'Desktop to open at startup',
      description:
        'The id of the desktop to open when the app starts — `desktop` is the empty floating one. Use `last` to reopen whichever desktop you were on. Whatever you pick, every other desktop keeps its arrangement.',
      type: 'string',
      default: DEFAULT_BOOT_WORKSPACE,
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
    { kind: 'taskbar.mode', items: () => modeMenuItems() },
    { kind: 'taskbar.theme', items: () => themeMenuItems(currentThemeId()) },
    { kind: 'shell.app', items: () => appMenuItems() },
  ],
};

/**
 * The desktop-paradigm menu, from the tray's ▦/❐ indicator.
 *
 * Making a new desktop is offered *first* and converting this one last, because
 * converting is the lossy operation: split ratios survive a flip only as the rects
 * they happened to occupy, and coming back cannot recover them. The wording says so
 * rather than presenting the two as equivalent.
 */
function modeMenuItems(): ContextMenuItem[] {
  const tiling = layoutStore.getSnapshot().frame.mode === 'tiling';
  return [
    {
      id: 'mode.newTiled',
      label: 'New tiled desktop',
      run: () => void registry.runCommand('workspace.new'),
    },
    {
      id: 'mode.newFloating',
      label: 'New floating desktop',
      run: () => void registry.runCommand('workspace.newFloating'),
    },
    {
      id: 'mode.saveAs',
      label: 'Save this arrangement as a desktop',
      run: () => void registry.runCommand('workspace.saveAs'),
    },
    {
      id: 'mode.convert',
      label: tiling ? 'Convert this desktop to floating' : 'Convert this desktop to tiling',
      detail: 'Rearranges everything; split sizes are not preserved',
      run: () => void toggleDesktopMode(),
    },
  ];
}

/**
 * The app menu, from the logo at the top-left.
 *
 * That button used to run `shell.home`, which could not do anything: `home` is
 * the desktop now, and the desktop is the only view the button is ever visible
 * from. So the one piece of permanent chrome in the corner every OS puts its
 * menu in did nothing at all. These are the app-level destinations — the things
 * that are about the *installation* rather than about what is on screen.
 */
export function appMenuItems(): ContextMenuItem[] {
  const { frame } = layoutStore.getSnapshot();
  const tiling = frame.mode === 'tiling';
  return [
    {
      id: 'shell.setup',
      label: 'Setup…',
      hint: 'Local model, account, connectors',
      run: () => void registry.runCommand('shell.setup'),
    },
    {
      id: 'settings.open',
      label: 'Settings',
      run: () => registry.openPanel('settings.home'),
    },
    { id: 'app.theme', label: 'Theme', run: () => {}, submenu: themeMenuItems(currentThemeId()) },
    { id: 'app.backdrop', label: 'Backdrop', run: () => {}, submenu: backdropMenuItems() },
    {
      id: 'shell.commandPalette',
      label: 'Search everything…',
      run: () => void registry.runCommand('shell.commandPalette'),
    },
    // The same menu the tray's ▦/❐ indicator opens, not a flip. You change
    // paradigm by switching to a desktop that has the one you want; this row
    // reports which kind you are on and offers to make another.
    {
      id: 'app.desktops',
      label: tiling ? 'Desktops (this one tiles)' : 'Desktops (this one floats)',
      run: () => {},
      submenu: modeMenuItems(),
    },
    {
      id: 'shell.oobe',
      label: 'Run first-run setup again',
      run: () => void registry.runCommand('shell.oobe'),
    },
  ];
}

/**
 * The backdrop picker, resolved from the registry at click time so a plugin's
 * backdrop appears without the menu knowing plugins exist. Shared by the
 * desktop's right-click menu and the app menu — two lists would drift.
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
  const tiling = frame.mode === 'tiling';
  const hasWindows = frame.windows.length > 0;
  return [
    { id: 'desktop.backdrop', label: 'Backdrop', run: () => {}, submenu: backdropMenuItems() },
    // The whole item is dropped, not disabled and not left as an empty submenu,
    // when there are no windows: there is no state in which "arrange nothing"
    // becomes meaningful from here, and a parent whose submenu is dropped is a
    // row that opens nothing and does nothing when clicked.
    ...(hasWindows
      ? [
          {
            id: 'desktop.arrange',
            label: 'Arrange windows',
            run: () => {},
            submenu: [
              {
                id: 'desktop.cascade',
                label: 'Cascade',
                run: () => void arrangeDesktop('cascade'),
              },
              { id: 'desktop.grid', label: 'Grid', run: () => void arrangeDesktop('grid') },
              {
                id: 'desktop.columns',
                label: 'Columns',
                run: () => void arrangeDesktop('columns'),
              },
              { id: 'desktop.rows', label: 'Rows', run: () => void arrangeDesktop('rows') },
            ],
          },
        ]
      : []),
    {
      id: 'desktop.desktops',
      label: tiling ? 'Desktops (this one tiles)' : 'Desktops (this one floats)',
      run: () => {},
      submenu: modeMenuItems(),
    },
  ];
}

/** Idempotent, like every other `registry.register` call. */
export function registerDesktopModule(): void {
  registry.register(desktopModule);
}
