import { useRef, useState } from 'react';
import type { SplitDirection } from '@horrible/core';

/**
 * Blender-style corner split grip. Drag it into the pane to split: the dominant
 * drag axis picks the orientation and the new region appears on the side you drag
 * toward (drag left → new pane on the left, down → below, …). On release past a
 * small threshold it calls `onSplit`; a tap does nothing. Resizing is handled by
 * dockview's native sashes — this only adds the split gesture the engine lacks.
 *
 * The grip is invisible until the pane is hovered (see `.ws-split-handle` in
 * styles.css) so it never competes with pane content; while dragging it mounts a
 * full-pane overlay that previews the split and captures the pointer.
 */
const THRESHOLD = 14; // px of drag before a split commits

/** Dominant-axis → which side the new region attaches to. null = below threshold. */
function directionFor(dx: number, dy: number): SplitDirection | null {
  if (Math.abs(dx) < THRESHOLD && Math.abs(dy) < THRESHOLD) return null;
  if (Math.abs(dx) >= Math.abs(dy)) return dx < 0 ? 'left' : 'right';
  return dy < 0 ? 'above' : 'below';
}

export function SplitHandle({ onSplit }: { onSplit: (direction: SplitDirection) => void }) {
  const [preview, setPreview] = useState<SplitDirection | null>(null);
  const dragging = useRef(false);
  const origin = useRef({ x: 0, y: 0 });

  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragging.current = true;
    origin.current = { x: e.clientX, y: e.clientY };
    (e.target as Element).setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragging.current) return;
    setPreview(directionFor(e.clientX - origin.current.x, e.clientY - origin.current.y));
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (!dragging.current) return;
    dragging.current = false;
    const direction = directionFor(e.clientX - origin.current.x, e.clientY - origin.current.y);
    setPreview(null);
    if (direction) onSplit(direction);
  };

  return (
    <>
      <div
        className="ws-split-handle"
        title="Drag to split this pane"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      />
      {preview && (
        <div className={`ws-split-preview ws-split-preview-${preview}`} aria-hidden="true" />
      )}
    </>
  );
}
