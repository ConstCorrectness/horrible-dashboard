/**
 * Design mode: build a transformer as a node graph and watch the PyTorch appear.
 *
 * This composes the four surfaces — palette, canvas, inspector, generated code — and
 * owns the one piece of state they share: the `DesignGraph`. Everything else is
 * derived from it, on the backend:
 *
 * - **shapes and parameter counts** come from `/graph/validate`, which is pure
 *   Python and instant, and which the pane labels as *our* arithmetic rather than
 *   torch's. A forward pass in a real venv is a separate, slower question.
 * - **the module source** comes from `/graph/code`. Nothing generates Python in
 *   TypeScript; there is one generator, and the file you copy out is the file the
 *   tests compile.
 *
 * Both are debounced together and fire on the same edit, so the numbers on the wires
 * and the code on the right always describe the same graph.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { CodePane } from './CodePane';
import {
  formatCount,
  getCatalog,
  getTemplate,
  loadDesign,
  nodeFromSpec,
  saveDesign,
  toCode,
  validateGraph,
  type Catalog,
  type CodeResult,
  type DesignGraph,
  type GraphNode,
  type Layout,
  type NodeSpec,
  type ShapeReport,
} from './graph';
import { Inspector } from './Inspector';
import { autoLayout } from './layout';
import { ModelCanvas } from './ModelCanvas';
import { Palette } from './Palette';

const EMPTY_LAYOUT: Layout = { nodes: {}, frames: [], viewport: {} };

/** The design a fresh pane opens on. Named, so a save has somewhere to go. */
const DEFAULT_NAME = 'Untitled';

function emptyGraph(): DesignGraph {
  return {
    name: 'MyModel',
    config: {
      vocab_size: 32000,
      d_model: 512,
      n_heads: 8,
      n_kv_heads: 8,
      ffn_hidden: 1376,
      n_layers: 6,
    },
    nodes: [],
    edges: [],
    groups: [],
  };
}

export function ModelDesigner() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [name, setName] = useState(DEFAULT_NAME);
  const [graph, setGraph] = useState<DesignGraph>(emptyGraph);
  const [layout, setLayout] = useState<Layout>(EMPTY_LAYOUT);
  const [report, setReport] = useState<ShapeReport | null>(null);
  const [code, setCode] = useState<CodeResult>({ source: '', markers: {}, error: null });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCost, setShowCost] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [saved, setSaved] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');

  const specs = useMemo(() => new Map((catalog?.nodes ?? []).map((s) => [s.type, s])), [catalog]);
  const selected = useMemo(
    () => graph.nodes.find((n) => n.id === selectedId) ?? null,
    [graph.nodes, selectedId],
  );

  // The catalog, once. Also the first load of whatever design was open — a pane
  // that opens on an empty canvas every time is a pane you stop using.
  useEffect(() => {
    let cancelled = false;
    void getCatalog()
      .then((next) => !cancelled && setCatalog(next))
      .catch(() => !cancelled && setNotice('Could not load the node catalog.'));
    void loadDesign(DEFAULT_NAME)
      .then((stored) => {
        if (cancelled) return;
        setName(stored.name);
        setGraph(stored.graph);
        setLayout(stored.layout);
      })
      .catch(() => {
        /* No saved design yet — an empty canvas is the correct starting state. */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // One debounce for both derivations, so the wires and the code never describe
  // different graphs.
  useEffect(() => {
    const handle = setTimeout(() => {
      void validateGraph(graph)
        .then(setReport)
        .catch(() => setReport(null));
      void toCode(graph)
        .then(setCode)
        .catch(() => setCode({ source: '', markers: {}, error: 'Could not reach the generator.' }));
    }, 180);
    return () => clearTimeout(handle);
  }, [graph]);

  // Saving is separate and slower: it writes two files and regenerates the module
  // on disk, which is not something to do on every keystroke.
  const firstSave = useRef(true);
  useEffect(() => {
    if (firstSave.current) {
      firstSave.current = false;
      return;
    }
    setSaved('saving');
    const handle = setTimeout(() => {
      void saveDesign(name, graph, layout)
        .then(() => setSaved('saved'))
        .catch(() => setSaved('error'));
    }, 700);
    return () => clearTimeout(handle);
  }, [graph, layout, name]);

  const addNode = useCallback((spec: NodeSpec) => {
    const node = nodeFromSpec(spec);
    setGraph((current) => ({ ...current, nodes: [...current.nodes, node] }));
    setSelectedId(node.id);
  }, []);

  const applyTemplate = useCallback((id: string) => {
    void getTemplate(id)
      .then((next) => {
        setGraph(next);
        // A template arrives with no positions, so it is laid out once and then
        // owned by the user — re-running dagre later would undo their arrangement.
        setLayout({ ...EMPTY_LAYOUT, nodes: autoLayout(next.nodes, next.edges) });
        setSelectedId(null);
      })
      .catch(() => setNotice('Could not load that template.'));
  }, []);

  const tidy = useCallback(() => {
    setLayout((current) => ({ ...current, nodes: autoLayout(graph.nodes, graph.edges) }));
  }, [graph]);

  const updateNode = useCallback((next: GraphNode) => {
    setGraph((current) => ({
      ...current,
      nodes: current.nodes.map((node) => (node.id === next.id ? next : node)),
    }));
  }, []);

  const deleteNode = useCallback((id: string) => {
    setGraph((current) => ({
      ...current,
      nodes: current.nodes.filter((node) => node.id !== id),
      edges: current.edges.filter((edge) => edge.source !== id && edge.target !== id),
    }));
    setSelectedId(null);
  }, []);

  const errors = (report?.issues ?? []).filter((issue) => issue.severity === 'error');

  return (
    <div className="mg-designer">
      <header className="mg-toolbar">
        <input
          className="mg-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Design name"
        />
        <span className={`mg-save mg-save-${saved}`}>
          {saved === 'saving'
            ? 'saving…'
            : saved === 'error'
              ? 'not saved'
              : saved === 'saved'
                ? 'saved'
                : ''}
        </span>

        <div className="mg-toolbar-spacer" />

        {report && (
          <span
            className="mg-stat"
            title="Counted from the graph, not measured from a running model."
          >
            <span className="mg-mono">{formatCount(report.totalParams)}</span> params
            <span className="mg-chip mg-chip-estimate">estimated</span>
          </span>
        )}
        {errors.length > 0 && (
          <span className="mg-stat mg-stat-bad">
            {errors.length} {errors.length === 1 ? 'problem' : 'problems'}
          </span>
        )}
        <button
          type="button"
          className={`mg-button${showCost ? ' mg-button-on' : ''}`}
          onClick={() => setShowCost((on) => !on)}
          title="Show each node's share of the parameter count."
        >
          Cost
        </button>
        <button type="button" className="mg-button" onClick={tidy}>
          Tidy
        </button>
      </header>

      {notice && (
        <div className="mg-notice" role="status" onClick={() => setNotice(null)}>
          {notice}
        </div>
      )}

      <div className="mg-body">
        <aside className="mg-left">
          <Palette
            specs={catalog?.nodes ?? []}
            templates={catalog?.templates ?? []}
            onAdd={addNode}
            onTemplate={applyTemplate}
          />
        </aside>

        <main className="mg-center">
          {graph.nodes.length === 0 ? (
            <div className="mg-blank">
              <h2 className="mg-blank-title">Nothing here yet</h2>
              <p>
                Pick a starting point on the left, or drop in an Input node and build outwards. A
                residual connection is an <strong>Add</strong> node with two wires into it — there
                is no hidden flag that makes one for you.
              </p>
            </div>
          ) : (
            <ModelCanvas
              graph={graph}
              layout={layout}
              report={report}
              specs={specs}
              showCost={showCost}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onGraphChange={setGraph}
              onLayoutChange={setLayout}
              onRefused={setNotice}
            />
          )}
        </main>

        <aside className="mg-right">
          <Inspector
            graph={graph}
            node={selected}
            spec={selected ? (specs.get(selected.type) ?? null) : null}
            params={selected ? report?.params[selected.id] : undefined}
            onNodeChange={updateNode}
            onGraphChange={setGraph}
            onDelete={deleteNode}
          />
          <CodePane
            source={code.source}
            error={code.error}
            markers={code.markers}
            highlightNode={selectedId}
          />
        </aside>
      </div>
    </div>
  );
}
