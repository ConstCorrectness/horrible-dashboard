/**
 * Node groups: entering one, creating one, and folding a selection into one.
 *
 * A group **is a generated `nn.Module` subclass** — that is the whole reason
 * Blender's metaphor fits a neural network, and it is why the canvas needs a notion
 * of *which graph you are looking at*. Blender calls it the context path; here it is
 * a list of group ids, and every edit the canvas makes is written back into whatever
 * that path names.
 *
 * All of it is pure: a graph in, a graph out, no React and no engine. The canvas is
 * unproven pixels until someone opens it in a real browser, but these decisions —
 * what a selection may legally become, where a boundary wire reconnects — are the
 * part that has to be right, so they live here and are tested.
 */
import {
  newGroupId,
  newNodeId,
  type DesignGraph,
  type GraphEdge,
  type GraphNode,
  type SubGraph,
} from './graph';

/** Node types that are a graph's own boundary, and so can never be inside a group. */
const TERMINALS = new Set(['io.input', 'io.output', 'io.group_input', 'io.group_output']);

export interface Scope {
  /** The path actually resolved — truncated if it named a group that is gone. */
  path: string[];
  /** The group being edited, or null at the root. */
  group: SubGraph | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/**
 * The graph level a context path names.
 *
 * A path segment naming a group that no longer exists **truncates** rather than
 * resolving to nothing: a group deleted in another tab should drop you back to the
 * level above, not leave you staring at an empty canvas that refuses every edit.
 */
export function resolveScope(graph: DesignGraph, path: string[]): Scope {
  let nodes = graph.nodes;
  let edges = graph.edges;
  let group: SubGraph | null = null;
  const resolved: string[] = [];

  for (const gid of path) {
    const found = graph.groups.find((g) => g.id === gid);
    if (!found) break;
    group = found;
    nodes = found.nodes;
    edges = found.edges;
    resolved.push(gid);
  }

  return { path: resolved, group, nodes, edges };
}

/** Write a level's nodes and edges back into the graph the path came from. */
export function withScope(
  graph: DesignGraph,
  path: string[],
  nodes: GraphNode[],
  edges: GraphEdge[],
): DesignGraph {
  const gid = path[path.length - 1];
  if (!gid) return { ...graph, nodes, edges };
  return {
    ...graph,
    groups: graph.groups.map((g) => (g.id === gid ? { ...g, nodes, edges } : g)),
  };
}

/** How many instances point at a group, anywhere in the design. */
export function groupUsage(graph: DesignGraph, gid: string): number {
  const scopes = [graph.nodes, ...graph.groups.map((g) => g.nodes)];
  let count = 0;
  for (const nodes of scopes) {
    for (const node of nodes) {
      if (node.type === 'group' && node.params.group === gid) count += 1;
    }
  }
  return count;
}

/**
 * A name no other group in the design is using.
 *
 * The backend refuses two groups that compile to the same class — the second
 * definition would silently replace the first — so a default of "Block" every time
 * would make the second group you create an error. Suggesting "Block 2" is friendlier
 * than explaining the collision.
 */
export function uniqueGroupName(graph: DesignGraph, desired: string): string {
  const taken = new Set(graph.groups.map((g) => g.name.trim().toLowerCase()));
  const stem = desired.trim() || 'Block';
  if (!taken.has(stem.toLowerCase())) return stem;
  for (let n = 2; n < 500; n += 1) {
    const candidate = `${stem} ${n}`;
    if (!taken.has(candidate.toLowerCase())) return candidate;
  }
  return `${stem} ${Date.now()}`;
}

function link(
  source: string,
  target: string,
  sourceHandle = 'out',
  targetHandle = 'in',
): GraphEdge {
  return {
    id: `${source}:${sourceHandle}->${target}:${targetHandle}`,
    source,
    sourceHandle,
    target,
    targetHandle,
  };
}

export interface GroupCreated {
  graph: DesignGraph;
  groupId: string;
  /** The instance node dropped into the current scope. */
  instanceId: string;
}

/**
 * A new, empty group — input wired straight to output — instantiated here.
 *
 * The wire between its two terminals is not a formality: a group whose output has
 * nothing connected to it cannot be generated at all, so an empty group would be a
 * broken design from the instant it was created.
 */
export function createGroup(graph: DesignGraph, path: string[], name = 'Block'): GroupCreated {
  const gid = newGroupId();
  const gin: GraphNode = { id: newNodeId('io.group_input'), type: 'io.group_input', params: {} };
  const gout: GraphNode = { id: newNodeId('io.group_output'), type: 'io.group_output', params: {} };
  const sub: SubGraph = {
    id: gid,
    name: uniqueGroupName(graph, name),
    nodes: [gin, gout],
    edges: [link(gin.id, gout.id)],
  };
  const instance: GraphNode = {
    id: newNodeId('group'),
    type: 'group',
    params: { group: gid, count: 1 },
  };

  const scope = resolveScope(graph, path);
  const withGroup: DesignGraph = { ...graph, groups: [...graph.groups, sub] };
  return {
    graph: withScope(withGroup, scope.path, [...scope.nodes, instance], scope.edges),
    groupId: gid,
    instanceId: instance.id,
  };
}

/** Drop a group definition. Refused while anything still instantiates it. */
export function deleteGroup(graph: DesignGraph, gid: string): DesignGraph | { error: string } {
  const used = groupUsage(graph, gid);
  if (used > 0) {
    return {
      error: `${used} node${used === 1 ? '' : 's'} still run${used === 1 ? 's' : ''} this group. Delete ${used === 1 ? 'it' : 'them'} first.`,
    };
  }
  return { ...graph, groups: graph.groups.filter((g) => g.id !== gid) };
}

export type Extraction = GroupCreated | { error: string };

export function isExtracted(result: Extraction): result is GroupCreated {
  return 'graph' in result;
}

/**
 * Blender's Ctrl-G: fold the selected nodes into a group and leave an instance
 * behind.
 *
 * The interesting part is the boundary. A group in this IR has exactly one input
 * and one output, because a `forward(self, x)` that takes one tensor and returns one
 * tensor is what everything downstream — the ×N stack especially — is built on. So a
 * selection with two independent tensors coming in, or two leaving, is **refused
 * with the count**, not silently wired to whichever one came first. Widening the
 * selection is the fix, and saying so is more use than a generic complaint.
 */
export function extractGroup(
  graph: DesignGraph,
  path: string[],
  ids: string[],
  name = 'Block',
): Extraction {
  const scope = resolveScope(graph, path);
  const inside = new Set(ids);
  const picked = scope.nodes.filter((n) => inside.has(n.id));

  if (picked.length === 0) return { error: 'Nothing is selected.' };
  const terminal = picked.find((n) => TERMINALS.has(n.type));
  if (terminal) {
    return {
      error:
        'A group cannot contain the graph’s own input or output — those are the boundary it sits inside, and it brings its own.',
    };
  }

  const inner = scope.edges.filter((e) => inside.has(e.source) && inside.has(e.target));
  const inbound = scope.edges.filter((e) => !inside.has(e.source) && inside.has(e.target));
  const outbound = scope.edges.filter((e) => inside.has(e.source) && !inside.has(e.target));
  const outside = scope.edges.filter((e) => !inside.has(e.source) && !inside.has(e.target));

  const feeders = new Set(inbound.map((e) => `${e.source}:${e.sourceHandle ?? 'out'}`));
  if (feeders.size > 1) {
    return {
      error: `The selection takes tensors from ${feeders.size} places outside it, and a group has one input. Widen the selection to take them in too.`,
    };
  }
  const exits = new Set(outbound.map((e) => `${e.source}:${e.sourceHandle ?? 'out'}`));
  if (exits.size > 1) {
    return {
      error: `${exits.size} nodes in the selection feed the rest of the graph, and a group returns one tensor. Widen the selection.`,
    };
  }

  const gid = newGroupId();
  const gin: GraphNode = { id: newNodeId('io.group_input'), type: 'io.group_input', params: {} };
  const gout: GraphNode = { id: newNodeId('io.group_output'), type: 'io.group_output', params: {} };

  // Every wire that crossed the boundary now crosses the group's own terminals, on
  // the same handles: a residual `Add` that took the block's input keeps taking it.
  const subEdges: GraphEdge[] = [
    ...inner,
    ...inbound.map((e) => link(gin.id, e.target, 'out', e.targetHandle ?? 'in')),
  ];
  const exit = outbound[0];
  if (exit) subEdges.push(link(exit.source, gout.id, exit.sourceHandle ?? 'out', 'in'));

  const sub: SubGraph = {
    id: gid,
    name: uniqueGroupName(graph, name),
    nodes: [gin, ...picked, gout],
    edges: subEdges,
  };

  const instance: GraphNode = {
    id: newNodeId('group'),
    type: 'group',
    params: { group: gid, count: 1 },
  };
  const feeder = inbound[0];
  const outerEdges: GraphEdge[] = [
    ...outside,
    ...(feeder ? [link(feeder.source, instance.id, feeder.sourceHandle ?? 'out', 'in')] : []),
    ...outbound.map((e) => link(instance.id, e.target, 'out', e.targetHandle ?? 'in')),
  ];
  const outerNodes = [...scope.nodes.filter((n) => !inside.has(n.id)), instance];

  const withGroup: DesignGraph = { ...graph, groups: [...graph.groups, sub] };
  return {
    graph: withScope(withGroup, scope.path, outerNodes, outerEdges),
    groupId: gid,
    instanceId: instance.id,
  };
}

/** Rename a group — which renames the class the generator emits for it. */
export function renameGroup(graph: DesignGraph, gid: string, name: string): DesignGraph {
  return {
    ...graph,
    groups: graph.groups.map((g) => (g.id === gid ? { ...g, name } : g)),
  };
}
