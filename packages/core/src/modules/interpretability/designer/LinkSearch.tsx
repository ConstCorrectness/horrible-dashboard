/**
 * Drag a wire into empty space and get a menu of what can go on the end of it.
 *
 * This is the single best interaction in Blender's node editor, and the reason is
 * that it inverts the question. The palette asks "which of twenty-one node types do
 * you want?"; this asks "what can accept the thing I am already holding?" — which is
 * a much smaller question, and one you are in the middle of answering anyway.
 *
 * The filtering is what makes it worth having. A node only appears if it has a
 * socket of the right type on the right side, so the list is already legal by
 * construction: there is no way to pick something from this menu and then be told
 * the connection is refused. Nothing converts implicitly here, so a list that
 * offered incompatible nodes would be offering mistakes.
 */
import { useEffect, useMemo, useRef, useState } from 'react';

import type { NodeSpec } from './graph';

export interface LinkSearchProps {
  /** Screen position to open at — where the wire was dropped. */
  at: { x: number; y: number };
  /** The socket type being dragged, so only compatible nodes are offered. */
  socketType: string;
  /** Dragging from an output looks for a node with a matching *input*, and vice versa. */
  wants: 'input' | 'output';
  specs: NodeSpec[];
  onPick: (spec: NodeSpec) => void;
  onClose: () => void;
}

export function LinkSearch({ at, socketType, wants, specs, onPick, onClose }: LinkSearchProps) {
  const [query, setQuery] = useState('');
  const box = useRef<HTMLDivElement | null>(null);
  const field = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    field.current?.focus();
  }, []);

  // Dismiss on a click anywhere else. Capture phase, so a click that lands on the
  // canvas behind closes the menu instead of starting a drag under it.
  useEffect(() => {
    const away = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) onClose();
    };
    document.addEventListener('mousedown', away, true);
    return () => document.removeEventListener('mousedown', away, true);
  }, [onClose]);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return specs
      .filter((spec) => {
        const sockets = wants === 'input' ? spec.inputs : spec.outputs;
        if (!sockets.some((s) => s.type === socketType)) return false;
        if (!needle) return true;
        return (
          spec.label.toLowerCase().includes(needle) ||
          spec.type.includes(needle) ||
          spec.doc.toLowerCase().includes(needle)
        );
      })
      .slice(0, 12);
  }, [specs, socketType, wants, query]);

  return (
    <div
      ref={box}
      className="mg-linksearch"
      style={{ left: at.x, top: at.y }}
      role="dialog"
      aria-label="Add a connected node"
    >
      <input
        ref={field}
        className="mg-search"
        placeholder={`node with a ${socketType} ${wants}`}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          // Local to a focused text field, not a shell binding: Escape closes and
          // Enter takes the first match, which is what every search box does.
          if (e.key === 'Escape') onClose();
          if (e.key === 'Enter' && matches[0]) onPick(matches[0]);
        }}
      />
      <div className="mg-linksearch-list">
        {matches.map((spec) => (
          <button
            key={spec.type}
            type="button"
            className={`mg-palette-item mg-cat-${spec.category}`}
            onClick={() => onPick(spec)}
            title={spec.doc}
          >
            {spec.label}
          </button>
        ))}
        {!matches.length && (
          <p className="mg-linksearch-empty">
            Nothing takes a <span className="mg-mono">{socketType}</span> there.
          </p>
        )}
      </div>
    </div>
  );
}
