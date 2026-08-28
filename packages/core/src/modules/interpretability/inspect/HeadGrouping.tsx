/**
 * Query heads drawn over the KV heads they share.
 *
 * The one part of a transformer whose cost is not obvious from a number: "32 heads
 * / 8 KV heads" is a 4x smaller KV cache than full MHA, and seeing four query ticks
 * sitting on each KV block makes that immediate.
 *
 * Moved here from `ModelExplorer.tsx` so it can be drawn twice — on the attention
 * node and in the detail pane — and so the geometry decisions live in `graph.ts`
 * where they are tested. Two fixes came with the move:
 *
 * - `.mx-heads` was capped at `max-width: 260px` to match the old fixed left
 *   column. That column is resizable now, so the cap only shrank it.
 * - `width` is a **viewBox** constant, not a pixel width. It always was one in
 *   effect — the element scales to its container — but it was named as if it were
 *   a size, which is why the CSS cap looked reasonable.
 */
import { headGroups } from './graph';
import type { AttentionSpec } from '../store';

/** SVG user units, not pixels. The element scales to whatever box it is given. */
const VIEW_W = 240;
const VIEW_H = 18;

export function HeadGrouping({ attention }: { attention: AttentionSpec }) {
  const spec = headGroups(attention);
  if (!spec) return null;

  const gap = 4;
  const groupW = (VIEW_W - gap * (spec.groups - 1)) / spec.groups;

  return (
    <svg
      className="mx-heads"
      viewBox={`0 0 ${VIEW_W + 12} ${VIEW_H}`}
      preserveAspectRatio="xMinYMid meet"
      role="img"
      aria-label={`${attention.heads ?? '?'} query heads over ${attention.kvHeads ?? '?'} KV heads`}
    >
      {Array.from({ length: spec.groups }, (_, g) => {
        const x = g * (groupW + gap);
        const qW = Math.max(1.5, (groupW - (spec.perGroup - 1) * 1.5) / spec.perGroup);
        return (
          <g key={g}>
            {Array.from({ length: spec.perGroup }, (_, q) => (
              <rect
                key={q}
                x={x + q * (qW + 1.5)}
                y={0}
                width={qW}
                height={7}
                rx={1}
                className="md-qhead"
              />
            ))}
            <rect x={x} y={9} width={groupW} height={5} rx={1} className="md-kvhead" />
          </g>
        );
      })}
      {spec.hidden > 0 && (
        <text x={VIEW_W + 4} y={12} className="md-micro">
          …
        </text>
      )}
    </svg>
  );
}
