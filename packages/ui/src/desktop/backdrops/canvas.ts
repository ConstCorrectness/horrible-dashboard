/**
 * Shared plumbing for the canvas-drawn backdrops (`grid`, `pulse`).
 *
 * Three things every one of them has to get right, gathered here so a new
 * backdrop cannot forget one:
 *
 * 1. **Theme tokens are read through `readThemeTokens` and the effect
 *    resubscribes on `useThemeId`.** A canvas cannot use a CSS variable, so it
 *    resolves the colour once — and a switch to another theme would otherwise
 *    leave the old palette painted until something else forced a remount. This
 *    is a repeat bug in this codebase; the hook makes it structural.
 * 2. **The loop stops when the document is hidden.** `requestAnimationFrame`
 *    does not fire in a hidden window at all, so a loop that drives its own
 *    clock off frame deltas resumes with a multi-minute jump. The hook passes an
 *    explicit, clamped `dt` and pauses on `visibilitychange` rather than relying
 *    on the frame callback to notice.
 * 3. **The backing store follows `devicePixelRatio`.** Without it the backdrop
 *    is visibly soft on every HiDPI screen.
 */
import { useEffect, useRef, type RefObject } from 'react';
import { readThemeTokens, useThemeId } from '@horrible/core';

export interface CanvasFrame {
  ctx: CanvasRenderingContext2D;
  /** CSS pixels, not backing-store pixels — the context is already scaled. */
  width: number;
  height: number;
  /** Seconds since the previous frame, clamped so a paused tab cannot jump. */
  dt: number;
  /** Seconds since the loop started, advanced by the same clamped `dt`. */
  t: number;
  tokens: Record<string, string>;
}

const MAX_DT = 1 / 20;

/**
 * Run `draw` on every animation frame against a canvas that fills its parent.
 *
 * `tokenNames` are theme custom properties (without the leading `--`), resolved
 * before each run of the effect and handed to `draw` in `tokens`.
 */
export function useCanvasBackdrop(
  tokenNames: readonly string[],
  draw: (frame: CanvasFrame) => void,
): RefObject<HTMLCanvasElement | null> {
  const ref = useRef<HTMLCanvasElement | null>(null);
  // The draw callback is almost always a fresh closure each render; keeping it
  // in a ref means the loop is not torn down and restarted sixty times a second
  // by a parent that re-renders.
  const drawRef = useRef(draw);
  drawRef.current = draw;
  const themeId = useThemeId();
  const tokenKey = tokenNames.join(',');

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const tokens = readThemeTokens(tokenNames);
    let width = 0;
    let height = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      width = Math.max(1, Math.round(rect.width));
      height = Math.max(1, Math.round(rect.height));
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      // setTransform, not scale: this runs on every resize and `scale` compounds.
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    let raf = 0;
    let last = performance.now();
    let t = 0;
    const tick = (now: number) => {
      const dt = Math.min(MAX_DT, Math.max(0, (now - last) / 1000));
      last = now;
      t += dt;
      drawRef.current({ ctx, width, height, dt, t, tokens });
      raf = requestAnimationFrame(tick);
    };

    const start = () => {
      if (raf) return;
      // Reset the clock on resume, so the first frame back is an ordinary one
      // rather than a jump proportional to how long the tab was hidden.
      last = performance.now();
      raf = requestAnimationFrame(tick);
    };
    const stop = () => {
      if (!raf) return;
      cancelAnimationFrame(raf);
      raf = 0;
    };
    const onVisibility = () => (document.hidden ? stop() : start());
    document.addEventListener('visibilitychange', onVisibility);
    if (!document.hidden) start();

    return () => {
      stop();
      observer.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
    };
    // `themeId` is a real dependency: it is what re-reads the tokens.
  }, [themeId, tokenKey]);

  return ref;
}
