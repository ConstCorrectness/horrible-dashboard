/**
 * The snap palette: one table describing every snap zone, its chord and its label.
 *
 * Data rather than three parallel lists, because the pieces that must agree are in
 * three different places — the `window.snap:<zone>` commands and their keybindings in
 * `module.tsx`, and the 3x3 grid `SnapAssist.tsx` draws. A zone added to one and
 * forgotten in another is a cell that lights up and does nothing, or a chord with no
 * hint; neither fails loudly.
 *
 * The chord is a **two-stroke sequence** (`mod+alt+s` then a letter). `mod+shift+arrow`
 * is already the halves family and `mod+alt+arrow` is `area.split:<dir>` in the frame
 * module — both are live at once on a tiling desktop, so a third modifier on either
 * risks a collision that only shows up on one platform. A prefix has room for all
 * thirteen zones and gives the palette something to render.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import type { SnapZone } from '@horrible/core';

/** The first stroke. Checked against `keymap/reserved.ts` for every platform/host. */
export const SNAP_PREFIX = 'mod+alt+s';

export interface SnapCell {
  zone: SnapZone;
  /** The second stroke — a bare letter or digit. */
  key: string;
  /** Command title and palette caption. */
  label: string;
}

/**
 * The nine positional zones, laid out the way the keys are: `q w e / a s d / z x c`
 * is the 3x3 block under the left hand, so the chord is the shape you want.
 */
export const SNAP_GRID: readonly (readonly SnapCell[])[] = [
  [
    { zone: 'tl', key: 'q', label: 'top left' },
    { zone: 'top', key: 'w', label: 'top half' },
    { zone: 'tr', key: 'e', label: 'top right' },
  ],
  [
    { zone: 'left', key: 'a', label: 'left half' },
    { zone: 'center', key: 's', label: 'centred' },
    { zone: 'right', key: 'd', label: 'right half' },
  ],
  [
    { zone: 'bl', key: 'z', label: 'bottom left' },
    { zone: 'bottom', key: 'x', label: 'bottom half' },
    { zone: 'br', key: 'c', label: 'bottom right' },
  ],
] as const;

/** Full-height vertical thirds — the wide-monitor arrangement, on the digit row. */
export const SNAP_THIRDS: readonly SnapCell[] = [
  { zone: 'third-l', key: '1', label: 'left third' },
  { zone: 'third-c', key: '2', label: 'middle third' },
  { zone: 'third-r', key: '3', label: 'right third' },
] as const;

export const SNAP_MAX: SnapCell = { zone: 'max', key: 'f', label: 'maximized' };

/**
 * Arrow synonyms for the four halves. The letters are the fast path once learned;
 * the arrows are what a first-time user presses after the palette appears, and a
 * palette that ignores them teaches that it does not work.
 */
export const SNAP_ARROWS: readonly { zone: SnapZone; key: string }[] = [
  { zone: 'left', key: 'left' },
  { zone: 'right', key: 'right' },
  { zone: 'top', key: 'up' },
  { zone: 'bottom', key: 'down' },
] as const;

/** Every cell with its own second stroke, in declaration order. */
export const SNAP_CELLS: readonly SnapCell[] = [...SNAP_GRID.flat(), ...SNAP_THIRDS, SNAP_MAX];

export const snapCommandId = (zone: SnapZone): string => `window.snap:${zone}`;
