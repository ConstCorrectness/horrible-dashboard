/**
 * A number that rolls to its new value instead of jumping.
 *
 * Seeded at the final value, so the first paint is already correct, and every
 * change is backed by a timeout that snaps to the target: `requestAnimationFrame`
 * does not fire in a background tab (or the app's hidden preview), and a stat that
 * reads `0` when it means `62` is worse than no animation at all.
 */
import { useEffect, useRef, useState } from 'react';

const DURATION_MS = 420;

export function RollingNumber({ value }: { value: number }) {
  const [shown, setShown] = useState(value);
  const from = useRef(value);

  useEffect(() => {
    const start = from.current;
    if (start === value) return;
    let frame = 0;
    const began = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - began) / DURATION_MS);
      const eased = 1 - (1 - t) ** 3;
      setShown(Math.round(start + (value - start) * eased));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    const snap = setTimeout(() => setShown(value), DURATION_MS + 60);
    from.current = value;
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(snap);
      setShown(value);
    };
  }, [value]);

  return <>{shown}</>;
}
