import { useEffect, useRef, useState } from 'react';

/**
 * A number that rolls to its new value instead of jumping.
 *
 * Seeded at the real value, so the first paint is already correct — the roll only
 * happens on a *change*. A timeout snaps to the target as well, because
 * `requestAnimationFrame` does not fire in a background tab: a counter reading
 * `0` when it means `38` is worse than no animation at all.
 */
export function useRollingNumber(target: number, durationMs = 600): number {
  const [shown, setShown] = useState(target);
  const fromRef = useRef(target);

  useEffect(() => {
    const from = fromRef.current;
    if (from === target) return;
    fromRef.current = target;

    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(Math.round(from + (target - from) * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    const snap = window.setTimeout(() => setShown(target), durationMs + 80);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(snap);
      setShown(target);
    };
  }, [target, durationMs]);

  return shown;
}
