/**
 * The snap palette: the 3x3 grid of zones shown while the `mod+alt+s` prefix is
 * pending.
 *
 * It owns **no keyboard handling**. The two-stroke bindings in `module.tsx` are the
 * only thing listening, and this is a pure renderer of the dispatcher's pending-chord
 * state — which is also why it cannot fall out of step with what the keys actually do.
 * Adding a `keydown` listener here would put a second claimant on the same strokes and
 * break the Escape ladder, which already abandons a half-typed chord on its first rung.
 *
 * Clicking a cell runs the same command the chord would, so the palette is reachable
 * with the mouse once it is open — a hint that can only be read is a hint people learn
 * once and then guess at.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import {
  matchesSpec,
  pendingChord,
  registry,
  rectForZone,
  tryParseSpec,
  type SnapZone,
} from '@horrible/core';

import {
  SNAP_GRID,
  SNAP_MAX,
  SNAP_PREFIX,
  SNAP_THIRDS,
  snapCommandId,
  type SnapCell,
} from './snap-palette';

const PREFIX_CHORD = tryParseSpec(SNAP_PREFIX);

/**
 * Is the prefix the dispatcher is holding *our* prefix?
 *
 * Matching on the hint string would compare `e.key` — `'s'` — against every other
 * sequence prefix that happens to end in the same letter. Comparing the actual event
 * to the parsed spec is the same test the resolver makes.
 */
function snapPrefixPending(): boolean {
  const pending = pendingChord();
  if (!PREFIX_CHORD || pending.length !== PREFIX_CHORD.length) return false;
  return pending.every((event, i) => matchesSpec(event, PREFIX_CHORD[i]));
}

function Cell({ cell }: { cell: SnapCell }) {
  // The same unit-surface trick `SnapOverlay` uses: the zone's own geometry drawn as
  // percentages, so a cell's thumbnail is the rect it will actually produce rather
  // than a picture of one drawn by hand.
  const r = rectForZone(cell.zone, { w: 100, h: 100 });
  return (
    <button
      type="button"
      className="os-snap-assist__cell"
      title={`Snap ${cell.label}`}
      onClick={() => void registry.runCommand(snapCommandId(cell.zone))}
    >
      <span className="os-snap-assist__thumb" aria-hidden>
        <span
          className="os-snap-assist__fill"
          style={{ left: `${r.x}%`, top: `${r.y}%`, width: `${r.w}%`, height: `${r.h}%` }}
        />
      </span>
      <kbd className="os-snap-assist__key">{cell.key}</kbd>
      <span className="os-snap-assist__label">{cell.label}</span>
    </button>
  );
}

/**
 * `hint` is the shell's pending-chord string. It is passed in rather than read here
 * because the dispatcher pushes it through a hook — this component re-renders when the
 * shell does, and then asks `pendingChord()` for the truth.
 */
export function SnapAssist({ hint }: { hint: string | null }) {
  if (!hint || !snapPrefixPending()) return null;
  const cells: SnapCell[] = [...SNAP_THIRDS, SNAP_MAX];
  return (
    <div className="os-snap-assist" role="dialog" aria-label="Snap window">
      <div className="os-snap-assist__grid">
        {SNAP_GRID.flat().map((cell: SnapCell) => (
          <Cell key={cell.zone} cell={cell} />
        ))}
      </div>
      <div className="os-snap-assist__row">
        {cells.map((cell) => (
          <Cell key={cell.zone} cell={cell} />
        ))}
      </div>
      <p className="os-snap-assist__foot">
        Arrows snap halves · <kbd>Esc</kbd> cancels
      </p>
    </div>
  );
}

export type { SnapZone };
