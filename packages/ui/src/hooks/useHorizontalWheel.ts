/**
 * Turn a vertical wheel into horizontal scrolling on a strip that only scrolls
 * sideways — the taskbar's window buttons, the workspace tabs, a window's tab
 * strip.
 *
 * All of those are `overflow-x: auto` with the scrollbar hidden, so before this
 * they could only be scrolled with a trackpad's horizontal gesture or
 * shift+wheel: on a mouse the overflowing content was simply unreachable, with
 * no scrollbar to hint that anything was there.
 *
 * Three things it has to get right:
 *
 * - **The listener is non-passive.** React's `onWheel` is registered passively
 *   on the root in some paths, where `preventDefault` is ignored with a console
 *   warning — so this attaches its own listener through a ref callback.
 * - **It only claims the event when the strip actually overflows.** A wheel over
 *   a half-empty taskbar must still scroll whatever is behind it; swallowing it
 *   unconditionally makes the page feel stuck near the edge of the screen.
 * - **A real horizontal delta is left alone.** A trackpad already sends `deltaX`
 *   and the browser already applies it; adding our own on top scrolls twice as
 *   far as the gesture asked for.
 *
 * It also stamps `data-overflowing` on the element while there is more content
 * than fits, so the stylesheet can fade the edges. That has to be measured
 * rather than assumed: an unconditional fade dims the first tab of a strip that
 * fits perfectly well, which reads as a rendering bug.
 */
import { useCallback, useEffect, useRef } from 'react';

export function useHorizontalWheel<T extends HTMLElement>(): (node: T | null) => void {
  const ref = useRef<T | null>(null);
  const observer = useRef<ResizeObserver | null>(null);

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    if (el.scrollWidth - el.clientWidth > 1) el.setAttribute('data-overflowing', '');
    else el.removeAttribute('data-overflowing');
  }, []);

  const onWheel = useCallback((event: WheelEvent) => {
    const el = ref.current;
    if (!el) return;
    // `scrollWidth` and `clientWidth` differ by less than a pixel on a strip
    // that fits; the slack keeps sub-pixel layout rounding from registering as
    // overflow and stealing every wheel event over the taskbar.
    if (el.scrollWidth - el.clientWidth <= 1) return;
    if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
    if (event.deltaY === 0) return;
    // `deltaMode` is lines (1) or pages (2) on some mice; a raw delta of 3 would
    // move three pixels. Normalizing to roughly a button's width per notch is
    // what makes one click of the wheel move one item rather than a hair.
    const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? el.clientWidth : 1;
    el.scrollLeft += event.deltaY * scale;
    event.preventDefault();
  }, []);

  // Deliberately no dependency array: the strip overflows because a button was
  // added to it, and the element itself never resizes when that happens, so a
  // ResizeObserver on it stays silent. Re-measuring after every render of the
  // owning component is one layout read and catches every case.
  useEffect(measure);

  useEffect(() => {
    return () => {
      ref.current?.removeEventListener('wheel', onWheel);
      observer.current?.disconnect();
    };
  }, [onWheel]);

  return useCallback(
    (node: T | null) => {
      ref.current?.removeEventListener('wheel', onWheel);
      observer.current?.disconnect();
      ref.current = node;
      if (!node) return;
      node.addEventListener('wheel', onWheel, { passive: false });
      // Covers the other direction: the content is unchanged but the strip got
      // narrower (a window resize, or a sibling zone growing).
      const ro = new ResizeObserver(measure);
      ro.observe(node);
      observer.current = ro;
      measure();
    },
    [measure, onWheel],
  );
}
