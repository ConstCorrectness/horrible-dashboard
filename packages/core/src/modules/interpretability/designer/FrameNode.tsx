/**
 * Frames: a labelled rectangle behind a set of nodes, and nothing more.
 *
 * Blender's frames carry no semantics, and neither do these — they live entirely in
 * the layout sidecar, they emit no code, and the generator has never heard of them.
 * That is the whole point: a canvas of forty nodes needs somewhere to write "this is
 * the attention branch" without that sentence becoming part of the model.
 *
 * The geometry is **derived, never stored**. A frame is a membership set
 * (`NodeLayout.frame`) and its rectangle is the bounding box of whatever currently
 * belongs to it, so dragging a member re-draws the frame around it for free. Storing
 * a rectangle as well would mean two facts about one thing, and the day they disagree
 * the frame is somewhere its contents are not.
 */
import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { FrameBox, Layout, NodeLayout } from './graph';
import { NODE_H, NODE_W } from './layout';

/** Room for the label above the box, and a margin so nodes are not flush to it. */
const PAD = 26;

export interface FrameNodeData {
  label: string;
  color: string;
  [key: string]: unknown;
}

export function FrameNode({ data }: NodeProps) {
  const d = data as FrameNodeData;
  return (
    <div className="mg-frame" style={d.color ? { borderColor: d.color } : undefined}>
      <span className="mg-frame-label" style={d.color ? { color: d.color } : undefined}>
        {d.label}
      </span>
      {/* React Flow expects every node type to be connectable-shaped; a frame is
          not, so its handles are hidden rather than absent — an absent handle
          makes the engine warn on every render. */}
      <Handle id="in" type="target" position={Position.Left} className="mg-frame-handle" />
      <Handle id="out" type="source" position={Position.Right} className="mg-frame-handle" />
    </div>
  );
}

export interface PlacedFrame {
  id: string;
  label: string;
  color: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Where each frame sits, given where its members currently are.
 *
 * A frame with no members present at this level is **dropped rather than drawn at
 * the origin**: its nodes are inside a group you are not looking at, and an empty
 * labelled box in the corner would be a thing to go hunting for.
 */
export function placeFrames(
  frames: FrameBox[],
  positions: Record<string, NodeLayout>,
  memberOf: (nodeId: string) => string | undefined,
  visible: string[],
): PlacedFrame[] {
  const out: PlacedFrame[] = [];
  for (const frame of frames) {
    const members = visible.filter((id) => memberOf(id) === frame.id && positions[id]);
    if (!members.length) continue;
    const xs = members.map((id) => positions[id]!.x);
    const ys = members.map((id) => positions[id]!.y);
    const x = Math.min(...xs) - PAD;
    const y = Math.min(...ys) - PAD;
    out.push({
      id: frame.id,
      label: frame.label,
      color: frame.color,
      x,
      y,
      width: Math.max(...xs) + NODE_W + PAD - x,
      height: Math.max(...ys) + NODE_H + PAD - y,
    });
  }
  return out;
}

/** Membership lookup over a layout, for `placeFrames`. */
export function frameMembership(layout: Layout): (nodeId: string) => string | undefined {
  return (nodeId) => layout.nodes[nodeId]?.frame || undefined;
}
