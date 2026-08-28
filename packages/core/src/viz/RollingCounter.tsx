/**
 * A number that counts toward its value instead of jumping to it.
 *
 * The rule that makes this safe, and the reason it is a component rather than four
 * copies of a `useEffect`: **seed at the final value and snap on a timeout.**
 * `requestAnimationFrame` does not fire in a backgrounded tab, so an animation that
 * starts at 0 and walks up leaves a tile reading `0` when it means `62` — for as
 * long as the tab stays in the background, which can be hours. A tile that is
 * wrong is worse than a tile that never animated.
 *
 * So: state starts at `value`. The animation runs only if rAF is actually firing,
 * and a `setTimeout` (which fires in a background tab) snaps to the truth
 * regardless. The worst case is "it did not animate", never "it shows the wrong
 * number".
 */
import { useEffect, useRef, useState } from 'react';

import './viz.css';

export interface RollingCounterProps {
  value: number;
  format?: (value: number) => string;
  /** How long a change takes to roll. */
  durationMs?: number;
  className?: string;
}

export function RollingCounter({
  value,
  format = (n) => String(Math.round(n)),
  durationMs = 480,
  className,
}: RollingCounterProps) {
  // Seeded at the truth, not at zero. See the header.
  const [shown, setShown] = useState(value);
  const from = useRef(value);

  useEffect(() => {
    const start = from.current;
    if (start === value) return;

    const reduced =
      typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      from.current = value;
      setShown(value);
      return;
    }

    const began = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - began) / durationMs);
      // Ease out: a linear roll reads as a machine, an eased one as a change.
      setShown(start + (value - start) * (1 - (1 - t) ** 3));
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);

    // The guarantee. `setTimeout` fires in a background tab where rAF does not, so
    // the number is correct even if the animation never ran a single frame.
    const snap = setTimeout(() => {
      cancelAnimationFrame(raf);
      setShown(value);
    }, durationMs + 60);

    from.current = value;
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(snap);
    };
  }, [value, durationMs]);

  return <span className={className ?? 'viz-counter'}>{format(shown)}</span>;
}
