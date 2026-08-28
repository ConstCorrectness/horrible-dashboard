/**
 * The Inspect canvas's node renderers.
 *
 * Two kinds only: an ordinary stage, and the stack — which is the interesting one,
 * because it carries the whole layer index inside a single node.
 *
 * ## Level of detail
 *
 * Every node reads the viewport zoom and drops its fact rows below a threshold.
 * Zooming out is then a way to see the *shape* of the model, and zooming in is how
 * you read its numbers, instead of the canvas offering one fixed density that is
 * too dense far out and too sparse close in.
 */
import { Handle, Position, useStore, type NodeProps } from '@xyflow/react';

import { HeadGrouping } from './HeadGrouping';
import type { InspectNode } from './graph';

/** Below this the facts are unreadable anyway, so they are noise. */
const DETAIL_ZOOM = 0.62;

export interface InspectNodeData extends Record<string, unknown> {
  node: InspectNode;
  selected: boolean;
  onPickLayer: (layer: number) => void;
}

function useDetailed(): boolean {
  return useStore((s) => s.transform[2] >= DETAIL_ZOOM);
}

function Stage({ data }: NodeProps) {
  const { node, selected } = data as unknown as InspectNodeData;
  const detailed = useDetailed();
  return (
    <div className={`ix-node ix-${node.kind}${selected ? ' ix-on' : ''}`}>
      <Handle type="target" position={Position.Top} className="ix-port" />
      <div className="ix-label">{node.label}</div>
      {node.sub && <div className="ix-sub">{node.sub}</div>}
      {detailed && node.attention && <HeadGrouping attention={node.attention} />}
      {detailed && node.facts.length > 0 && (
        <div className="ix-facts">
          {node.facts.map((fact) => (
            <span key={fact}>{fact}</span>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="ix-port" />
    </div>
  );
}

/**
 * The stack: one node, N ticks.
 *
 * Tick **height** is that block's bytes, so a model whose later blocks are larger
 * says so. Tick **band** is its shape signature, so a model that alternates its
 * attention shape — Gemma 4 does — draws as stripes instead of as a tooltip.
 */
function Stack({ data }: NodeProps) {
  const { node, selected, onPickLayer } = data as unknown as InspectNodeData;
  const rail = node.rail;
  const detailed = useDetailed();

  return (
    <div className={`ix-node ix-stack${selected ? ' ix-on' : ''}`}>
      <Handle type="target" position={Position.Top} className="ix-port" />
      <div className="ix-label">
        {node.label} <b>{node.sub}</b>
      </div>

      {rail && rail.count > 0 && (
        <div
          className="ix-rail"
          role="group"
          aria-label={`${rail.count} decoder blocks`}
          /* The wheel belongs to the canvas: a rail that swallowed it would make
             the model un-zoomable wherever the pointer happened to be. */
          onWheel={undefined}
        >
          {rail.ticks.map((tick) => (
            <button
              key={tick.index}
              type="button"
              className={`ix-tick${rail.selected === tick.index ? ' ix-tick-on' : ''}`}
              data-band={tick.band}
              style={{ height: `${tick.weight * 100}%` }}
              title={`Block ${tick.index}${rail.bands > 1 ? ` · shape group ${tick.band + 1}` : ''}`}
              onClick={(event) => {
                event.stopPropagation();
                onPickLayer(tick.index);
              }}
            >
              <span className="ix-sr">Block {tick.index}</span>
            </button>
          ))}
        </div>
      )}

      {detailed && (
        <div className="ix-facts">
          {rail?.selected !== null && rail?.selected !== undefined ? (
            <span>block {rail.selected} open</span>
          ) : (
            <span>pick a block</span>
          )}
          {node.facts.map((fact) => (
            <span key={fact} className="ix-warn">
              {fact}
            </span>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="ix-port" />
    </div>
  );
}

export const NODE_TYPES = { stage: Stage, stack: Stack };
