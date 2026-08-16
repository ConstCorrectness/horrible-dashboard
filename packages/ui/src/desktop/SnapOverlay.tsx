/**
 * The translucent preview of where a snap would land, drawn while a window is
 * dragged near an edge.
 *
 * It sizes itself with the *same* `rectForZone` the drop uses, so the preview cannot
 * promise one rect and the drop deliver another — which is the failure this shares a
 * function to prevent. Percentages rather than pixels, so it needs no measurement of
 * its own: the layer it fills is already the coordinate space.
 */
import { rectForZone, type SnapZone } from '@horrible/core';

export function SnapOverlay({ zone }: { zone: SnapZone }) {
  // A unit surface turns the zone's geometry directly into percentages.
  const r = rectForZone(zone, { w: 100, h: 100 });
  return (
    <div
      className="os-snap-overlay"
      aria-hidden
      style={{ left: `${r.x}%`, top: `${r.y}%`, width: `${r.w}%`, height: `${r.h}%` }}
    />
  );
}
