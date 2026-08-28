/**
 * A resizable two-way split, inside a pane.
 *
 * `packages/ui/src/layout/Sash.tsx` is the app's other splitter and is not this
 * one: it takes a `SplitNode` and dispatches `SET_SPLIT_SIZES` to `layoutStore`,
 * because it divides the *workspace*. A pane's own two columns are not part of the
 * layout tree, and every consumer here is a core module — `layout/use-sections.ts`
 * says core must not import ui, so it could not be reused even if it fit.
 *
 * Before this, in-pane splits were fixed pixel widths in CSS. The Model Explorer's
 * architecture diagram lived in `minmax(190px, 260px)`, so it stayed 260px wide in
 * a pane three times that — the diagram could not be made bigger by any means the
 * app offered.
 *
 * ## Two things here are load-bearing
 *
 * **The drag does not `setState`.** A pointermove at 60 Hz that re-renders both
 * children makes a React Flow canvas rebuild its node list sixty times a second,
 * and the drag stutters against the pointer. So the live size is written to a CSS
 * custom property on the container element directly and React is told once, on
 * pointerup.
 *
 * **`narrowBelow` measures the container, not the viewport.** The rule this
 * replaces was a `@media (width <= 720px)` on `.mx-body`, which is wrong by
 * construction: a pane docked in a three-column workspace is narrow at any viewport
 * width, and a maximised pane is wide on a small screen.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';

import './splitpane.css';
import { clampSize, getSplitSize, setSplitSize } from './split-sizes';

export interface SplitPaneProps {
  /** Persistence key. Stable across mounts — it is what remembers the size. */
  id: string;
  orientation?: 'row' | 'column';
  /** Which child the stored size measures. */
  side?: 'start' | 'end';
  /** Opening size in px for the measured side. */
  initial: number;
  /** Floor for the measured side. */
  min: number;
  /** Floor for the flexible side. */
  minOther: number;
  /** Container extent under which the two children stack and the handle is hidden. */
  narrowBelow?: number;
  /** Accessible name for the separator — "Diagram width", not "Splitter". */
  label: string;
  children: [ReactNode, ReactNode];
}

/** One arrow press. Coarse enough to be worth pressing, fine enough to aim with. */
const NUDGE = 16;

export function SplitPane({
  id,
  orientation = 'row',
  side = 'start',
  initial,
  min,
  minOther,
  narrowBelow,
  label,
  children,
}: SplitPaneProps) {
  const horizontal = orientation === 'row';
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState(() => getSplitSize(id) ?? initial);
  const [narrow, setNarrow] = useState(false);
  const sizeRef = useRef(size);
  sizeRef.current = size;

  // Re-read on an id change: one component instance can be reused for a different
  // split (a pane switching modes), and carrying the old width across would apply
  // one surface's remembered size to another's.
  const opening = useRef(initial);
  opening.current = initial;
  useEffect(() => {
    // Read through a ref so `initial` is not a dependency: a caller computing it
    // inline would otherwise reset the user's drag on every render.
    setSize(getSplitSize(id) ?? opening.current);
  }, [id]);

  const extent = useCallback(() => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return 0;
    return horizontal ? rect.width : rect.height;
  }, [horizontal]);

  /** Push a size to the DOM without going through React. */
  const paint = useCallback((next: number) => {
    ref.current?.style.setProperty('--split-size', `${next}px`);
  }, []);

  useLayoutEffect(() => paint(size), [size, paint]);

  useEffect(() => {
    const host = ref.current;
    if (!host || narrowBelow === undefined || typeof ResizeObserver !== 'function') return;
    const observer = new ResizeObserver(([entry]) => {
      const box = entry.contentRect;
      setNarrow((horizontal ? box.width : box.height) < narrowBelow);
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, [narrowBelow, horizontal]);

  const commit = useCallback(
    (next: number) => {
      sizeRef.current = next;
      setSize(next);
      setSplitSize(id, next);
    },
    [id],
  );

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const span = extent();
    if (span <= 0) return;

    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);
    const origin = horizontal ? event.clientX : event.clientY;
    const from = sizeRef.current;
    let latest = from;

    const onMove = (move: PointerEvent) => {
      const delta = (horizontal ? move.clientX : move.clientY) - origin;
      // Dragging the end-side handle right makes that side SMALLER: the stored
      // size measures the child, not the handle's position from the left edge.
      latest = clampSize(from + (side === 'start' ? delta : -delta), span, min, minOther);
      paint(latest);
    };
    const onUp = () => {
      handle.releasePointerCapture(event.pointerId);
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onUp);
      handle.removeEventListener('pointercancel', onUp);
      commit(latest);
    };
    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
    handle.addEventListener('pointercancel', onUp);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const span = extent();
    const grow = horizontal ? 'ArrowRight' : 'ArrowDown';
    const shrink = horizontal ? 'ArrowLeft' : 'ArrowUp';
    // Off the ref, not off `size`: a held arrow key repeats faster than React
    // re-renders, and every repeat in one batch would read the same stale size and
    // land on the same result — the key would appear to move the handle once.
    const from = sizeRef.current;
    let next: number | null = null;
    if (event.key === grow) next = from + (side === 'start' ? NUDGE : -NUDGE);
    else if (event.key === shrink) next = from - (side === 'start' ? NUDGE : -NUDGE);
    else if (event.key === 'Home') next = min;
    else if (event.key === 'End') next = span - minOther;
    if (next === null) return;
    event.preventDefault();
    commit(clampSize(next, span, min, minOther));
  };

  const span = extent();
  return (
    <div
      ref={ref}
      className="hd-split"
      data-orientation={orientation}
      data-side={side}
      data-narrow={narrow || undefined}
    >
      <div className="hd-split-a">{children[0]}</div>
      <div
        className="hd-split-handle"
        role="separator"
        tabIndex={0}
        aria-orientation={horizontal ? 'vertical' : 'horizontal'}
        aria-label={label}
        aria-valuenow={Math.round(size)}
        aria-valuemin={min}
        aria-valuemax={Math.round(Math.max(min, span - minOther))}
        onPointerDown={onPointerDown}
        onKeyDown={onKeyDown}
        onDoubleClick={() => commit(clampSize(initial, span, min, minOther))}
        title="Drag to resize · double-click to reset"
      />
      <div className="hd-split-b">{children[1]}</div>
    </div>
  );
}
