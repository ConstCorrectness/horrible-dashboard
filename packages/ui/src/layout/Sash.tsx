/**
 * The divider between two siblings of a split. Dragging it re-balances the two
 * adjacent children's fractions (dispatching SET_SPLIT_SIZES), measured against
 * the flex container's live pixel extent so the drag tracks the pointer 1:1.
 */
import { useRef } from 'react';
import { layoutStore, MIN_FRACTION, type SplitNode } from '@horrible/core';

export function Sash({ split, index }: { split: SplitNode; index: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const horizontal = split.orientation === 'row';

  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    const container = ref.current?.parentElement;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const extent = horizontal ? rect.width : rect.height;
    if (extent <= 0) return;
    const start = horizontal ? e.clientX : e.clientY;
    const startSizes = [...split.sizes];

    const onMove = (me: PointerEvent) => {
      const delta = ((horizontal ? me.clientX : me.clientY) - start) / extent;
      const sizes = [...startSizes];
      const grow = Math.max(
        MIN_FRACTION,
        Math.min(sizes[index - 1] + delta, sizes[index - 1] + sizes[index] - MIN_FRACTION),
      );
      sizes[index] = sizes[index - 1] + sizes[index] - grow;
      sizes[index - 1] = grow;
      layoutStore.dispatch({ type: 'SET_SPLIT_SIZES', splitId: split.id, sizes });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  return (
    <div
      ref={ref}
      className={`frame-sash frame-sash--${horizontal ? 'v' : 'h'}`}
      onPointerDown={onPointerDown}
    />
  );
}
