import { describe, expect, it } from 'vitest';

import {
  newNodeId,
  socketsCompatible,
  formatShape,
  type GraphEdge,
  type GraphNode,
  type Layout,
  type SocketSpec,
} from '../designer/graph';
import { autoLayout, NODE_W, resolvePositions } from '../designer/layout';

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
