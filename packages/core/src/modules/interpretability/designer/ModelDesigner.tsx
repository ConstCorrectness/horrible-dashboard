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
 *
 * It also owns the **context path** — Blender's breadcrumb, which group you are
 * inside. The canvas is handed one graph *level* and knows nothing about groups;
 * everything about entering, creating and folding them lives in `scope.ts` as pure
 * functions, and this component is the part that turns them into buttons.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { CodePane } from './CodePane';
import {
  formatCount,
  getCatalog,
  getTemplate,
  listProjects,
  loadDesign,
  nodeFromSpec,
  parseSource,
  probeGraph,
  saveDesign,
  toCode,
  validateGraph,
  type Catalog,
  type CodeResult,
  type DesignGraph,
  type GraphEdge,
  type GraphNode,
  type Layout,
  type NodeLayout,
  type NodeSpec,
  type ProbeResult,
  type ShapeReport,
  type SubGraph,
  type TrainingProject,
} from './graph';
import { Inspector } from './Inspector';
import { autoLayout } from './layout';
import { ModelCanvas } from './ModelCanvas';
import { Palette } from './Palette';
import { ProbeBar } from './ProbeBar';
import {
  createGroup,
  deleteGroup,
  extractGroup,
  isExtracted,
  renameGroup,
  resolveScope,
  withScope,
} from './scope';

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
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  /** Which group we are inside, outermost first. Empty is the root graph. */
  const [path, setPath] = useState<string[]>([]);
  const [showCost, setShowCost] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [saved, setSaved] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [sync, setSync] = useState<{
    status: 'idle' | 'syncing' | 'ok' | 'error';
    message?: string;
  }>({ status: 'idle' });
  const [projects, setProjects] = useState<TrainingProject[]>([]);
  const [project, setProject] = useState('');
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [probing, setProbing] = useState(false);

  const specs = useMemo(() => new Map((catalog?.nodes ?? []).map((s) => [s.type, s])), [catalog]);
  const groupNames = useMemo(
    () => new Map(graph.groups.map((g) => [g.id, g.name])),
    [graph.groups],
  );

  // The level being edited. `resolveScope` truncates a path that names a group which
  // no longer exists, so deleting a group you are standing in drops you to the level
  // above rather than leaving an empty canvas that refuses every edit.
  const scope = useMemo(() => resolveScope(graph, path), [graph, path]);
  useEffect(() => {
    if (scope.path.length !== path.length) setPath(scope.path);
  }, [scope.path, path.length]);

  const selected = useMemo(
    () => scope.nodes.find((n) => n.id === selectedId) ?? null,
    [scope.nodes, selectedId],
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
    void listProjects()
      .then((next) => !cancelled && setProjects(next.projects ?? []))
      .catch(() => {
        /* No training module reachable: the probe reports "could not ask", which is
           the honest state and needs no separate error here. */
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

  /**
   * Edit whichever level the breadcrumb names.
   *
   * Always resolved against the graph in the updater rather than the render's
   * closure: a stale path would otherwise write a group's nodes into a group that no
   * longer exists, and the edit would vanish with no error anywhere.
   */
  const editScope = useCallback(
    (edit: (nodes: GraphNode[], edges: GraphEdge[]) => [GraphNode[], GraphEdge[]]) => {
      setGraph((current) => {
        const at = resolveScope(current, path);
        const [nodes, edges] = edit(at.nodes, at.edges);
        return withScope(current, at.path, nodes, edges);
      });
    },
    [path],
  );

  const setScope = useCallback(
    (nodes: GraphNode[], edges: GraphEdge[]) => editScope(() => [nodes, edges]),
    [editScope],
  );

  const addNode = useCallback(
    (spec: NodeSpec) => {
      const node = nodeFromSpec(spec);
      editScope((nodes, edges) => [[...nodes, node], edges]);
      setSelectedId(node.id);
    },
    [editScope],
  );

  const applyTemplate = useCallback((id: string) => {
    void getTemplate(id)
      .then((next) => {
        setGraph(next);
        // A template arrives with no positions, so it is laid out once and then
        // owned by the user — re-running dagre later would undo their arrangement.
        setLayout({ ...EMPTY_LAYOUT, nodes: autoLayout(next.nodes, next.edges) });
        setSelectedId(null);
        setPath([]);
      })
      .catch(() => setNotice('Could not load that template.'));
  }, []);

  // Tidies the level you are looking at, and only it: laying out a group's insides
  // would move nodes the user cannot see.
  const tidy = useCallback(() => {
    setLayout((current) => ({
      ...current,
      nodes: { ...current.nodes, ...autoLayout(scope.nodes, scope.edges) },
    }));
  }, [scope]);

  const updateNode = useCallback(
    (next: GraphNode) => {
      editScope((nodes, edges) => [
        nodes.map((node) => (node.id === next.id ? next : node)),
        edges,
      ]);
    },
    [editScope],
  );

  const deleteNode = useCallback(
    (id: string) => {
      editScope((nodes, edges) => [
        nodes.filter((node) => node.id !== id),
        edges.filter((edge) => edge.source !== id && edge.target !== id),
      ]);
      setSelectedId(null);
    },
    [editScope],
  );

  // Selection is reported as a set as well as a single node, because grouping acts
  // on the set. Held only when it actually changed — React Flow reports on every
  // interaction, and a fresh array each time would re-run everything downstream.
  const handleSelection = useCallback((ids: string[]) => {
    setSelectedIds((prev) =>
      prev.length === ids.length && prev.every((id, i) => id === ids[i]) ? prev : ids,
    );
  }, []);

  /** Blender's Tab: step into the group a node instantiates. */
  const enterGroup = useCallback(
    (nodeId: string) => {
      const node = scope.nodes.find((n) => n.id === nodeId);
      if (!node || node.type !== 'group') return;
      const gid = String(node.params.group ?? '');
      if (!graph.groups.some((g) => g.id === gid)) {
        setNotice('That instance points at a group this design no longer has.');
        return;
      }
      setPath((current) => [...current, gid]);
      setSelectedId(null);
    },
    [scope.nodes, graph.groups],
  );

  /** A new, empty group, instantiated here and entered. */
  const newGroup = useCallback(() => {
    const made = createGroup(graph, path);
    setGraph(made.graph);
    setPath([...path, made.groupId]);
    setSelectedId(null);
  }, [graph, path]);

  /**
   * Blender's Ctrl-G. The instance lands where the selection was, so the drawing
   * keeps its shape — a group that appeared at the origin would send you hunting.
   */
  const groupSelection = useCallback(() => {
    const picked = selectedIds.filter((id) => scope.nodes.some((n) => n.id === id));
    const result = extractGroup(graph, path, picked);
    if (!isExtracted(result)) {
      setNotice(result.error);
      return;
    }
    const placed = picked
      .map((id) => layout.nodes[id])
      .filter((p): p is NodeLayout => p !== undefined);
    if (placed.length) {
      const x = placed.reduce((sum, p) => sum + p.x, 0) / placed.length;
      const y = placed.reduce((sum, p) => sum + p.y, 0) / placed.length;
      setLayout((current) => ({
        ...current,
        nodes: { ...current.nodes, [result.instanceId]: { x, y } },
      }));
    }
    setGraph(result.graph);
    setSelectedId(result.instanceId);
    setNotice(
      `Grouped ${picked.length} node${picked.length === 1 ? '' : 's'}. Double-click the block to edit it.`,
    );
  }, [graph, path, selectedIds, scope.nodes, layout.nodes]);

  const changeGroup = useCallback(
    (next: SubGraph) => setGraph((current) => renameGroup(current, next.id, next.name)),
    [],
  );

  const removeGroup = useCallback(
    (gid: string) => {
      const next = deleteGroup(graph, gid);
      if ('error' in next) {
        setNotice(next.error);
        return;
      }
      setGraph(next);
    },
    [graph],
  );

  /**
   * A code edit, read back onto the canvas.
   *
   * Positions survive because the parser recovers node ids from the
   * `# horrible:node=` markers, so the layout sidecar still describes the graph — a
   * round trip that re-laid-out the canvas would make the code pane unusable for
   * anything but a glance. Nodes the edit genuinely introduced have no saved
   * position and are placed by `resolvePositions`.
   *
   * A file we cannot read changes nothing. The graph stays, the draft stays, and the
   * reason is shown: half a graph silently replacing a whole one is the worst
   * outcome available here.
   */
  const syncFromCode = useCallback((edited: string) => {
    setSync({ status: 'syncing' });
    void parseSource(edited)
      .then((result) => {
        if (result.error || !result.graph) {
          setSync({ status: 'error', message: result.error ?? 'Nothing in that file is a model.' });
          return;
        }
        setGraph(result.graph);
        const opaque = result.opaque.length
          ? ` ${result.opaque.length} class${result.opaque.length === 1 ? '' : 'es'} kept as custom code: ${result.opaque.join(', ')}.`
          : '';
        setSync({ status: 'ok', message: `Read back into the graph.${opaque}` });
      })
      .catch(() => setSync({ status: 'error', message: 'Could not reach the parser.' }));
  }, []);

  /**
   * The measurement. Manual on purpose: a cold torch import dominates it, so this is
   * a button you press when you want the truth, not something that fires as you type.
   *
   * A stale result is cleared the moment the graph changes — a measurement shown
   * beside a graph it did not measure is exactly the confident wrongness this whole
   * pane exists to refuse.
   */
  const runProbe = useCallback(() => {
    setProbing(true);
    void probeGraph(graph, project)
      .then(setProbe)
      .catch(() =>
        setProbe({
          status: 'unavailable',
          message: 'Could not reach the backend to run it.',
          traceback: '',
          outputShape: [],
          totalParams: null,
          estimatedParams: null,
          agrees: null,
          project,
          torchVersion: '',
          durationMs: 0,
        }),
      )
      .finally(() => setProbing(false));
  }, [graph, project]);

  useEffect(() => {
    setProbe(null);
  }, [graph]);

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

      {/* Blender's context path. The root crumb is the model's class; every one
          after it is a group you stepped into, and clicking one steps back out. */}
      <nav className="mg-breadcrumb" aria-label="Group context">
        <button
          type="button"
          className={`mg-crumb${path.length === 0 ? ' mg-crumb-here' : ''}`}
          onClick={() => {
            setPath([]);
            setSelectedId(null);
          }}
        >
          {graph.name || 'Model'}
        </button>
        {path.map((gid, index) => (
          <span key={gid} className="mg-crumb-row">
            <span className="mg-crumb-sep">/</span>
            <button
              type="button"
              className={`mg-crumb${index === path.length - 1 ? ' mg-crumb-here' : ''}`}
              onClick={() => {
                setPath(path.slice(0, index + 1));
                setSelectedId(null);
              }}
            >
              {graph.groups.find((g) => g.id === gid)?.name ?? gid}
            </button>
          </span>
        ))}

        <div className="mg-toolbar-spacer" />

        <button
          type="button"
          className="mg-button"
          onClick={groupSelection}
          disabled={selectedIds.length === 0}
          title="Fold the selected nodes into a group — one generated nn.Module subclass, instantiated where they were."
        >
          Group selection
        </button>
        <button
          type="button"
          className="mg-button"
          onClick={newGroup}
          title="Add an empty group here and step into it."
        >
          New group
        </button>
      </nav>

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
          {scope.nodes.length === 0 ? (
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
              nodes={scope.nodes}
              edges={scope.edges}
              layout={layout}
              report={report}
              specs={specs}
              groupNames={groupNames}
              showCost={showCost}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onSelectionChange={handleSelection}
              onScopeChange={setScope}
              onLayoutChange={setLayout}
              onEnter={enterGroup}
              onRefused={setNotice}
            />
          )}
        </main>

        <aside className="mg-right">
          <Inspector
            graph={graph}
            group={scope.group}
            node={selected}
            spec={selected ? (specs.get(selected.type) ?? null) : null}
            params={selected ? report?.params[selected.id] : undefined}
            onNodeChange={updateNode}
            onGraphChange={setGraph}
            onGroupChange={changeGroup}
            onDeleteGroup={removeGroup}
            onEnterGroup={enterGroup}
            onDelete={deleteNode}
          />
          <ProbeBar
            projects={projects}
            project={project}
            onProject={setProject}
            onRun={runProbe}
            running={probing}
            result={probe}
          />
          <CodePane
            source={code.source}
            error={code.error}
            markers={code.markers}
            highlightNode={selectedId}
            onSync={syncFromCode}
            syncState={sync}
          />
        </aside>
      </div>
    </div>
  );
}
