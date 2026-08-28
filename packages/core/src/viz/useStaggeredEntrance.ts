/**
 * Staggered entrance for a list, as props rather than as a per-component effect.
 *
 * The design notes ask for entrance transitions with a per-item delay. The trap
 * they also name is what makes this a hook instead of a CSS class: **the stagger
 * has to be capped.** A linear `delay = i * step` over a 200-row catalogue is a
 * five-second arrival, during which the list is a half-drawn thing you cannot use.
 * The cap is applied in CSS (`min(var(--stagger-i), N)`) so it costs nothing per
 * item and cannot be forgotten at a call site.
 *
 * Everything real is in `viz.css`; this only stamps the index. Two reasons it is
 * not simply `style={{animationDelay}}`:
 *
 * - the duration and easing stay tokens (`--stagger-step`, `--ease-entrance`),
 *   which is what keeps six themes able to disagree about motion; and
 * - `prefers-reduced-motion` is honoured once, in the stylesheet, rather than at
 *   every call site that remembers to check it.
 */
import type { CSSProperties } from 'react';

export interface EntranceProps {
  'data-enter': true;
  style: CSSProperties;
}

/**
 * Props for the item at `index`.
 *
 * Spread onto the element. A caller that already has a `style` should spread this
 * one after its own, or the custom property is lost and every item arrives at once.
 */
export function entranceProps(index: number, extra?: CSSProperties): EntranceProps {
  return {
    'data-enter': true,
    // A custom property rather than `animationDelay` directly: the cap and the
    // step token are applied in CSS, where they are one rule instead of N.
    style: { ...extra, ['--stagger-i' as string]: index },
  };
}

/**
 * The same thing for a whole list, when a caller would rather map once.
 *
 * Returns a getter rather than an array so it does not allocate per render for
 * lists that are re-rendered on a poll — which, in this module, is all of them.
 */
export function useStaggeredEntrance(): (index: number, extra?: CSSProperties) => EntranceProps {
  return entranceProps;
}
