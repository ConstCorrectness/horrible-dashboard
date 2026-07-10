/**
 * Blender-style corner grip: drag INTO the area to split it (dominant axis picks
 * the orientation, the new area appears on the side you drag toward); drag OUT
 * past the area's edge to join — the neighbor on that side is absorbed. The
 * evolution of the old SplitHandle, now with join. On release below the
 * threshold nothing happens.
 */
import { useRef, useState } from 'react';
import {
  joinAreaDirection,
  splitAreaBy,
  type NavDirection,
  type SplitDirection,
} from '@horrible/core';

const THRESHOLD = 14; // px of drag before a gesture commits

function splitDirectionFor(dx: number, dy: number): SplitDirection | null {
  if (Math.abs(dx) < THRESHOLD && Math.abs(dy) < THRESHOLD) return null;
  if (Math.abs(dx) >= Math.abs(dy)) return dx < 0 ? 'left' : 'right';
  return dy < 0 ? 'above' : 'below';
}

const NAV_FOR_SPLIT: Record<SplitDirection, NavDirection> = {
  left: 'left',
  right: 'right',
  above: 'up',
  below: 'down',
};

export function CornerGrip({ areaId }: { areaId: string }) {
  const [preview, setPreview] = useState<{ dir: SplitDirection; join: boolean } | null>(null);
  const dragging = useRef(false);
  const origin = useRef({ x: 0, y: 0 });
  const bounds = useRef<DOMRect | null>(null);

  const gestureFor = (e: { clientX: number; clientY: number }) => {
    const dir = splitDirectionFor(e.clientX - origin.current.x, e.clientY - origin.current.y);
    if (!dir) return null;
    // Outside the area's own bounds = join the neighbor on that side.
    const b = bounds.current;
    const outside =
      !!b &&
      (e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom);
    return { dir, join: outside };
  };

  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragging.current = true;
    origin.current = { x: e.clientX, y: e.clientY };
    bounds.current =
      (e.currentTarget as Element).closest('.frame-area')?.getBoundingClientRect() ?? null;
    (e.target as Element).setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragging.current) return;
    setPreview(gestureFor(e));
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (!dragging.current) return;
    dragging.current = false;
    const gesture = gestureFor(e);
    setPreview(null);
    if (!gesture) return;
    if (gesture.join) joinAreaDirection(areaId, NAV_FOR_SPLIT[gesture.dir]);
    else splitAreaBy(areaId, gesture.dir);
  };

  return (
    <>
      <div
        className="frame-corner-grip"
        title="Drag in to split, out to join"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      />
      {preview && (
        <div
          className={`frame-split-preview frame-split-preview--${preview.dir}${preview.join ? ' frame-split-preview--join' : ''}`}
          aria-hidden="true"
        />
      )}
    </>
  );
}
