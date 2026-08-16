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
  layoutStore,
  registry,
  setBackdrop,
  setDesktopMode,
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
import { DEFAULT_TASKBAR, TASKBAR_SETTING_KEY } from './Taskbar';
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
      id: 'desktop.toggleMode',
      title: 'Desktop: Toggle tiling / floating',
      run: () => void toggleDesktopMode(),
    },
    {
      id: 'desktop.tiling',
      title: 'Desktop: Switch to tiling',
      run: () => void setDesktopMode('tiling'),
    },
    {
      id: 'desktop.floating',
      title: 'Desktop: Switch to floating windows',
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
    { key: 'mod+alt+t', command: 'desktop.toggleMode' },
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
        'Which zones the taskbar shows and in what order, as JSON. Zones: start, windows, spacer, desktops, tray, clock. Also `position` (bottom/top), `showLabels`, `autoHide`.',
      type: 'string',
      default: JSON.stringify(DEFAULT_TASKBAR),
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
    { kind: 'taskbar.theme', items: () => themeMenuItems(currentThemeId()) },
  ],
};

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
    {
      id: 'desktop.backdrop',
      label: 'Backdrop',
      run: () => {},
      // Resolved from the registry at click time, so a plugin's backdrop is in
      // the menu without the menu knowing plugins exist.
      submenu: registry.backdrops.map((b) => ({
        id: `desktop.backdrop:${b.id}`,
        label: b.title,
        hint: b.description,
        checked: frame.backdrop.id === b.id,
        run: () => void setBackdrop({ id: b.id }),
      })),
    },
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
      id: 'desktop.toggleMode',
      label: tiling ? 'Switch to floating windows' : 'Switch to tiling',
      checked: tiling,
      run: () => void toggleDesktopMode(),
    },
  ];
}

/** Idempotent, like every other `registry.register` call. */
export function registerDesktopModule(): void {
  registry.register(desktopModule);
}
