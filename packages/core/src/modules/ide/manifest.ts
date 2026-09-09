/**
 * The workbench's **declarative** half — its region strips, keybindings and pane
 * metadata, with no component and no command handlers.
 *
 * Split out because the rules encoded here are exactly the ones worth a test, and
 * `index.tsx` cannot be imported by one: it reaches `../editor`, which opens a
 * WebSocket at module scope and dies under vitest's node environment. Data has no
 * such problem, so this file is the seam that makes the region rules testable at
 * all rather than checked by eye.
 */
import type { KeybindingDecl, PaneCaptureDecl, RegionViewDecl } from '@horribledashboard/sdk';

export const WORKBENCH_VIEW = 'ide.workbench';

/**
 * The sidebars. Three rules govern this array and each one is a silent bug:
 *
 * - **A view id may appear at most once.** `setRegionView` and the pick resolver
 *   both match first-by-id, so a second entry is simply unreachable.
 * - **The right strip must hold exactly one view.** `Region.tsx` tabs the left
 *   and bottom strips, but renders a right strip of two or more views as equal
 *   vertical bands — so a second entry there squashes the agent rather than
 *   giving it a tab.
 * - **No key may be `t`, `n` or `b`.** Those are the universal position toggles;
 *   the registry drops a violation with a console warning, and the letters are
 *   shared with any sections the pane declares.
 *
 * What is *not* here is as deliberate as what is. `code.search` stays out
 * because `mod+p` already opens the symbol-search modal, and a second symbol
 * surface inside the pane is the competing home the `embedded` flag exists to
 * prevent.
 */
export const IDE_REGIONS: RegionViewDecl[] = [
  {
    id: 'files.tree',
    label: 'Explorer',
    icon: '🗀',
    key: 'e',
    position: 'left',
    defaultOpen: true,
    defaultSize: 280,
  },
  { id: 'ide.textSearch', label: 'Search', icon: '⌕', key: 'f', position: 'left' },
  { id: 'ide.scm', label: 'Source Control', icon: '⎇', key: 'v', position: 'left' },
  { id: 'code.outline', label: 'Outline', icon: '≡', key: 'o', position: 'left' },
  { id: 'git.provenance', label: 'Provenance', icon: '⌥', key: 'g', position: 'left' },
  { id: 'agent.chat', label: 'Agent', icon: '🤖', key: 'a', position: 'right', defaultSize: 420 },
  {
    id: 'terminal.instance',
    label: 'Terminal',
    icon: '❯',
    key: 'j',
    position: 'bottom',
    defaultOpen: true,
    defaultSize: 260,
  },
  { id: 'ide.problems', label: 'Problems', icon: '⚠', key: 'm', position: 'bottom' },
];

/**
 * Keybindings.
 *
 * The modifier chords duplicate region picks the frame already synthesizes as
 * plain letters, and both exist on purpose: with `capture: keyboard` an
 * *unmodified* binding is suppressed while the pane has focus — deliberately, so
 * that typing `t` in a buffer does not toggle a strip — which would otherwise
 * make the letters dead exactly where the pane is being used. `capturePassthrough`
 * is what carries the modified ones through.
 */
export const IDE_KEYBINDINGS: KeybindingDecl[] = [
  { key: 'mod+shift+i', command: 'ide.open' },
  ...(
    [
      ['mod+shift+e', 'region.pick:files.tree'],
      ['mod+shift+f', 'region.pick:ide.textSearch'],
      ['mod+shift+g', 'region.pick:ide.scm'],
      ['mod+shift+m', 'region.pick:ide.problems'],
      ['mod+shift+j', 'region.pick:terminal.instance'],
      ['mod+shift+a', 'region.pick:agent.chat'],
    ] as const
  ).map(([key, command]) => ({
    key,
    command,
    when: `paneFocus == '${WORKBENCH_VIEW}'`,
    capturePassthrough: true,
  })),
  // Desktop only: the browser takes `mod+w` and declares it unpreventable in
  // `keymap/reserved.ts`, so a browser default here would never fire.
  {
    key: 'mod+w',
    command: 'ide.closeTab',
    when: `paneFocus == '${WORKBENCH_VIEW}'`,
    hosts: ['desktop'],
    capturePassthrough: true,
  },
];

/** The command ids the manifest declares, for the keybinding cross-check. */
export const IDE_COMMAND_IDS = [
  'ide.open',
  'ide.findInFiles',
  'ide.showScm',
  'ide.showProblems',
  'ide.toggleTerminal',
  'ide.toggleAgent',
  'ide.showExplorer',
  'ide.closeTab',
  'ide.nextTab',
  'ide.prevTab',
] as const;

/**
 * The workbench pane's metadata, minus its component — so a test can assert on
 * it without importing `index.tsx`.
 *
 * `title` is "Workbench" rather than "Editor" or "IDE": the registry warns on a
 * duplicate view title and "Editor" is `editor.buffer`'s.
 *
 * `capture` is the modern spelling of that pane's deprecated `editor: true`.
 * It is what makes plain-letter bindings stop firing while you type here, and it
 * is also why the modifier chords above exist.
 */
export const IDE_PANE_META = {
  title: 'Workbench',
  role: 'document' as const,
  icon: '🧰',
  singleton: true,
  capture: { mode: 'keyboard', escape: 'passthrough' } as PaneCaptureDecl,
};

/** The agent tools this module contributes. The orchestrator groups tools by
 * **name prefix**, so every one of these must begin `ide.` to form one group. */
export const IDE_TOOL_NAMES = [
  'ide.open',
  'ide.openFile',
  'ide.listOpenFiles',
  'ide.closeFile',
  'ide.findInFiles',
  'ide.problems',
  'ide.scmStatus',
  'ide.diff',
  'ide.stage',
  'ide.unstage',
] as const;
