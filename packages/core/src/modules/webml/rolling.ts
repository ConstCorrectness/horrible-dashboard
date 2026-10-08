/**
 * A number that rolls toward its target instead of jumping (tok/s, TTFT).
 *
 * Seeded at the target, and a timeout snaps to it: `requestAnimationFrame` does not
 * fire in a background tab or a hidden pane, and a stat reading 0 when it means 62
 * is worse than no animation (CLAUDE.local.md §5).
 */
import { useEffect, useRef, useState } from 'react';

export function useRollingNumber(target: number, durationMs = 400): number {
  const [value, setValue] = useState(target);
  const from = useRef(target);

  useEffect(() => {
    const start = from.current;
    if (start === target) return;
    const began = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - began) / durationMs);
      const eased = 1 - (1 - t) ** 3;
      const v = start + (target - start) * eased;
      from.current = v;
      setValue(v);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    const snap = setTimeout(() => {
      cancelAnimationFrame(frame);
      from.current = target;
      setValue(target);
    }, durationMs + 50);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(snap);
    };
  }, [target, durationMs]);

  return value;
}
