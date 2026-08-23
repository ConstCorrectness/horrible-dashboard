import { describe, expect, it } from 'vitest';

import {
  newNodeId,
  socketsCompatible,
  formatShape,
  type DesignGraph,
  type GraphEdge,
  type GraphNode,
  type Layout,
  type SocketSpec,
} from '../designer/graph';
import { autoLayout, NODE_W, resolvePositions } from '../designer/layout';
import {
  createGroup,
  deleteGroup,
  extractGroup,
  groupUsage,
  isExtracted,
  resolveScope,
  withScope,
} from '../designer/scope';

/**
 * The designer's pure logic. The canvas itself is a React Flow surface and is
 * verified in the running app; these are the decisions that happen *before* the
 * engine sees anything — where a node lands, and whether a wire is allowed at all.
 */

function node(id: string, type = 'norm.rms'): GraphNode {
  return { id, type, params: {} };
}

function edge(source: string, target: string): GraphEdge {
  return { id: `${source}->${target}`, source, target };
}

function socket(over: Partial<SocketSpec> = {}): SocketSpec {
  return { name: 'in', type: 'tensor', multi: false, label: 'in', ...over };
}

describe('socket typing', () => {
  it('refuses a link between different socket types', () => {
    // Blender converts a float to a colour implicitly; nothing converts implicitly
    // here, because silently reinterpreting a tensor is the failure this pane exists
    // to prevent.
    expect(socketsCompatible(socket({ type: 'tensor' }), socket({ type: 'int' }))).toBe(false);
    expect(socketsCompatible(socket({ type: 'tensor' }), socket({ type: 'tensor' }))).toBe(true);
  });

  it('refuses a link when either end is missing', () => {
    expect(socketsCompatible(undefined, socket())).toBe(false);
    expect(socketsCompatible(socket(), undefined)).toBe(false);
  });
});

describe('node ids', () => {
  it('are unique, because the backend keys shapes and code markers by them', () => {
    const ids = new Set(Array.from({ length: 500 }, () => newNodeId('norm.rms')));
    expect(ids.size).toBe(500);
  });
});

describe('layout', () => {
  it('lays a chain out left to right', () => {
    const nodes = [node('a'), node('b'), node('c')];
    const edges = [edge('a', 'b'), edge('b', 'c')];
    const placed = autoLayout(nodes, edges);
    expect(placed.a.x).toBeLessThan(placed.b.x);
    expect(placed.b.x).toBeLessThan(placed.c.x);
  });

  it('keeps every saved position when one node is new', () => {
    // The single most annoying thing a node editor can do is re-arrange the whole
    // canvas because one node appeared.
    const saved: Layout = {
      nodes: { a: { x: 10, y: 20 }, b: { x: 300, y: 20 } },
      frames: [],
      viewport: {},
    };
    const nodes = [node('a'), node('b'), node('c')];
    const placed = resolvePositions(nodes, [edge('a', 'b')], saved);
    expect(placed.a).toEqual({ x: 10, y: 20 });
    expect(placed.b).toEqual({ x: 300, y: 20 });
    expect(placed.c).toBeDefined();
  });

  it('drops a new node clear of the existing drawing', () => {
    const saved: Layout = { nodes: { a: { x: 0, y: 0 } }, frames: [], viewport: {} };
    const placed = resolvePositions([node('a'), node('b')], [], saved);
    expect(placed.b.x).toBeGreaterThan(NODE_W);
  });

  it('lays everything out when nothing has been saved', () => {
    const placed = resolvePositions([node('a'), node('b')], [edge('a', 'b')], undefined);
    expect(Object.keys(placed).sort()).toEqual(['a', 'b']);
  });
});

describe('shape labels', () => {
  it('renders symbolic and concrete dimensions the same way', () => {
    expect(formatShape(['B', 'T', 2048])).toBe('[B, T, 2048]');
  });

  it('renders nothing for an unknown shape rather than empty brackets', () => {
    expect(formatShape(undefined)).toBe('');
    expect(formatShape([])).toBe('');
  });
});

describe('node groups', () => {
  function design(): DesignGraph {
    return {
      name: 'MyModel',
      config: { d_model: 512 },
      nodes: [node('in', 'io.input'), node('a'), node('b'), node('out', 'io.output')],
      edges: [edge('in', 'a'), edge('a', 'b'), edge('b', 'out')],
      groups: [],
    };
  }

  it('resolves the root when the path is empty', () => {
    const scope = resolveScope(design(), []);
    expect(scope.group).toBeNull();
    expect(scope.nodes).toHaveLength(4);
  });

  it('truncates a path naming a group that is gone rather than blanking the canvas', () => {
    // A group deleted under you should drop you to the level above. Resolving to
    // nothing would leave a canvas that shows no nodes and silently swallows edits.
    const scope = resolveScope(design(), ['grp_missing']);
    expect(scope.path).toEqual([]);
    expect(scope.nodes).toHaveLength(4);
  });

  it('writes an edit back into the group the path names, not the root', () => {
    const made = createGroup(design(), []);
    const inside = resolveScope(made.graph, [made.groupId]);
    const added = [...inside.nodes, node('extra')];
    const next = withScope(made.graph, [made.groupId], added, inside.edges);
    expect(next.groups[0].nodes).toHaveLength(3);
    expect(next.nodes).toHaveLength(5); // untouched: 4 + the instance
  });

  it('creates a group already wired input to output', () => {
    // An empty group cannot be generated at all — codegen refuses a group whose
    // output has nothing connected — so it would be broken from the instant it
    // existed.
    const made = createGroup(design(), []);
    const sub = made.graph.groups[0];
    expect(sub.nodes.map((n) => n.type)).toEqual(['io.group_input', 'io.group_output']);
    expect(sub.edges).toHaveLength(1);
    expect(made.graph.nodes.some((n) => n.id === made.instanceId && n.type === 'group')).toBe(true);
  });

  it('never suggests a name another group already generates', () => {
    const first = createGroup(design(), []);
    const second = createGroup(first.graph, []);
    expect(second.graph.groups[1].name).not.toBe(second.graph.groups[0].name);
  });

  it('folds a selection into a group and reconnects both boundaries', () => {
    const result = extractGroup(design(), [], ['a', 'b']);
    if (!isExtracted(result)) throw new Error(result.error);
    const sub = result.graph.groups[0];
    const gin = sub.nodes.find((n) => n.type === 'io.group_input')!;
    const gout = sub.nodes.find((n) => n.type === 'io.group_output')!;

    // Inside: the boundary wires now come from and go to the group's own terminals.
    expect(sub.edges).toContainEqual(expect.objectContaining({ source: gin.id, target: 'a' }));
    expect(sub.edges).toContainEqual(expect.objectContaining({ source: 'b', target: gout.id }));
    expect(sub.edges).toContainEqual(expect.objectContaining({ source: 'a', target: 'b' }));

    // Outside: the instance stands exactly where the selection did.
    expect(result.graph.nodes.map((n) => n.id).sort()).toEqual(
      ['in', 'out', result.instanceId].sort(),
    );
    expect(result.graph.edges).toContainEqual(
      expect.objectContaining({ source: 'in', target: result.instanceId }),
    );
    expect(result.graph.edges).toContainEqual(
      expect.objectContaining({ source: result.instanceId, target: 'out' }),
    );
  });

  it('refuses a selection needing two inputs, and says how many', () => {
    // A group is a `forward(self, x)` — one tensor in, one out. Wiring a second
    // external source to whichever socket came first is the silent reinterpretation
    // this whole pane exists to refuse.
    const graph = design();
    graph.nodes.push(node('side'));
    graph.edges.push(edge('side', 'b'));
    const result = extractGroup(graph, [], ['a', 'b']);
    expect(isExtracted(result)).toBe(false);
    if (!isExtracted(result)) expect(result.error).toContain('2 places');
  });

  it('refuses to swallow the graph’s own input or output', () => {
    const result = extractGroup(design(), [], ['in', 'a']);
    expect(isExtracted(result)).toBe(false);
  });

  it('refuses to delete a group something still runs', () => {
    const made = createGroup(design(), []);
    expect(deleteGroup(made.graph, made.groupId)).toHaveProperty('error');
    const orphaned = { ...made.graph, nodes: made.graph.nodes.filter((n) => n.type !== 'group') };
    expect(deleteGroup(orphaned, made.groupId)).not.toHaveProperty('error');
  });

  it('counts instances across every level, not just the root', () => {
    const outer = createGroup(design(), []);
    const inner = createGroup(outer.graph, [outer.groupId]);
    expect(groupUsage(inner.graph, inner.groupId)).toBe(1);
  });
});
