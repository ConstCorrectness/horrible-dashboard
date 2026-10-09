/**
 * Lineage: every agent, the harness revisions it went through, and the runs each
 * revision produced — one zoomable tree, with a runs-over-time inset linked to it.
 *
 * Built after the Fast Gemma Challenge lineage plot, and fast for the same reasons:
 *
 * - **Pan and zoom never re-render.** `d3-zoom` owns the gesture and its handler
 *   writes one `transform` attribute on one `<g>`. React renders the tree only when
 *   the data, the level or the spread changes.
 * - **Hover and focus are class toggles** on marks that already exist; nothing is
 *   laid out again.
 * - **The inset is an overlay, not a pane.** `pointer-events: none` on its box and
 *   `all` on its points, so wheel and drag fall straight through it to the tree.
 *
 * The fit is applied without a transition on purpose: d3 transitions run on
 * `requestAnimationFrame`, which never fires in a hidden window, and a fit that
 * only lands on the next frame leaves the tree at the origin there.
 */
import {
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { select } from 'd3-selection';
import { zoom as d3zoom, zoomIdentity, type ZoomBehavior } from 'd3-zoom';

import { RollingNumber, type RowKind } from '../../../DataList';
import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { listHarnesses, listRuns, type Harness, type TrajectoryRun } from '../api';
import { RefreshIcon } from '../icons';
import { ago, Loading, ms, SectionShell, tokens, usd } from './common';
import {
  buildLineage,
  fitTransform,
  isGraded,
  linkPath,
  METRIC_IS_LOG,
  metricValue,
  neighbourhood,
  placeLineage,
  shortModel,
  verdictOf,
  yAxis,
  type Level,
  type Lineage,
  type LineageRow,
  type PlacedNode,
  type Verdict,
  type YMetric,
} from './lineage-model';
import './lineage.css';

/** The list endpoint's ceiling. Past it the oldest runs drop out of the tree. */
const RUN_LIMIT = 500;
/** Room the fit leaves for labels beyond the outermost node centres (user units). */
const LABEL_LEFT = 110;
const LABEL_RIGHT = 90;
/** Entrance stagger cap, so a large tree does not take seconds to finish arriving. */
const STAGGER_CAP = 36;

const VERDICT_KIND: Record<Verdict, RowKind> = {
  success: 'ok',
  failure: 'fail',
  partial: 'warn',
  running: 'warn',
  ungraded: 'idle',
  abandoned: 'idle',
};

interface CardAt {
  key: string;
  x: number;
  y: number;
}

export function LineageSection({
  onOpenRun,
  onInspectHarness,
}: {
  onOpenRun: (runId: string) => void;
  onInspectHarness: (fingerprint: string) => void;
}) {
  const [runs, setRuns] = useState<TrajectoryRun[]>([]);
  const [harnesses, setHarnesses] = useState<Harness[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [level, setLevel] = useState<Level>('runs');
  const [spread, setSpread] = useState(1);
  const [metric, setMetric] = useState<YMetric>('steps');
  const [focus, setFocus] = useState<string | null>(null);
  const [card, setCard] = useState<CardAt | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  const stageRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const viewRef = useRef<SVGGElement>(null);
  const zoomRef = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Set by a user pan or zoom. A resize re-fits only while this is false, so
  // dragging a splitter does not throw away a view somebody zoomed into.
  const userMoved = useRef(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [runPage, harnessList] = await Promise.all([
        listRuns({ limit: RUN_LIMIT }),
        listHarnesses(),
      ]);
      setRuns(runPage.runs);
      setTotal(runPage.total);
      setHarnesses(harnessList);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const lineage = useMemo(() => buildLineage(runs, harnesses, level), [runs, harnesses, level]);
  // Quantized, so dragging a splitter relays the tree a handful of times rather
  // than on every pixel.
  const aspect = size.h
    ? Math.round((Math.max(1, size.w - insetReserve(size.w)) / size.h) * 4) / 4
    : 0;
  const placement = useMemo(
    () =>
      placeLineage(lineage, {
        rowGap: level === 'runs' ? 15 : 34,
        colGap: (level === 'runs' ? 96 : 120) * spread,
        aspect: aspect || undefined,
      }),
    [lineage, level, spread, aspect],
  );
  const rowByKey = useMemo(() => new Map(lineage.rows.map((r) => [r.key, r])), [lineage]);

  // --- stage size: measured once, then kept by ResizeObserver (which never fires
  // in a hidden window — the window listener is the fallback there).
  useLayoutEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const measure = () => setSize({ w: stage.clientWidth, h: stage.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    window.addEventListener('resize', measure);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, []);

  // --- zoom: created once; its handler is the only thing that moves the view.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const behavior = d3zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.05, 12])
      // A click that moved a few pixels is still a click, not a pan.
      .clickDistance(4)
      .on('zoom', (event: { transform: { toString(): string }; sourceEvent: unknown }) => {
        // `sourceEvent` is null for a programmatic transform (the fit).
        if (event.sourceEvent) userMoved.current = true;
        viewRef.current?.setAttribute('transform', event.transform.toString());
      });
    zoomRef.current = behavior;
    select(svg).call(behavior).on('dblclick.zoom', null);
    return () => {
      select(svg).on('.zoom', null);
    };
  }, []);

  const fit = useCallback(() => {
    const svg = svgRef.current;
    const behavior = zoomRef.current;
    if (!svg || !behavior || !size.w || !size.h || !placement.nodes.length) return;
    // The bounds are node centres; agent labels hang off to the left and harness
    // labels to the right, and a fit that ignored them would clip both.
    const { x0, x1, y0, y1 } = placement.bounds;
    const withLabels = { x0: x0 - LABEL_LEFT, x1: x1 + LABEL_RIGHT, y0, y1 };
    const t = fitTransform(withLabels, size.w - insetReserve(size.w), size.h, {
      maxScale: 1.8,
    });
    userMoved.current = false;
    select(svg).call(behavior.transform, zoomIdentity.translate(t.x, t.y).scale(t.k));
  }, [placement, size.w, size.h]);

  // New data, level or spread is a new picture and always re-fits; only a resize
  // defers to a view the user has moved. Declared before the fit effect so it
  // runs first.
  useEffect(() => {
    userMoved.current = false;
  }, [lineage, spread]);

  useEffect(() => {
    if (!userMoved.current) fit();
  }, [fit]);

  // --- hover, focus, card -------------------------------------------------------
  const active = focus ?? card?.key ?? null;
  const lit = useMemo(() => (active ? neighbourhood(lineage, active) : null), [lineage, active]);

  const showCard = useCallback((key: string, event: MouseEvent) => {
    clearTimeout(hideTimer.current);
    const rect = stageRef.current?.getBoundingClientRect();
    if (!rect) return;
    setCard({ key, x: event.clientX - rect.left, y: event.clientY - rect.top });
  }, []);

  const scheduleHide = useCallback(() => {
    clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(() => {
      // A focused node keeps its card: it is the thing being read.
      setCard((current) => (current && current.key === focus ? current : null));
    }, 180);
  }, [focus]);

  useEffect(() => () => clearTimeout(hideTimer.current), []);

  const toggleFocus = useCallback(
    (key: string, event: MouseEvent) => {
      event.stopPropagation();
      if (focus === key) {
        setFocus(null);
        return;
      }
      setFocus(key);
      showCard(key, event);
    },
    [focus, showCard],
  );

  const clear = useCallback(() => {
    setFocus(null);
    setCard(null);
  }, []);

  // A pinned card for a row the current level no longer draws (a run, after
  // switching to harnesses) would describe nothing on screen.
  useEffect(() => {
    if (focus && !rowByKey.has(focus)) setFocus(null);
    if (card && !rowByKey.has(card.key)) setCard(null);
  }, [rowByKey, focus, card]);

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') clear();
  };

  // --- derived figures ------------------------------------------------------------
  const failed = runs.filter((r) => verdictOf(r) === 'failure').length;
  const harnessCount = lineage.rows.filter((r) => r.kind === 'harness').length;
  const agentCount = lineage.rows.filter((r) => r.kind === 'agent').length;
  const activeRow = active ? rowByKey.get(active) : undefined;

  const delegationPaths = useMemo(() => {
    if (!activeRow) return [];
    const mine = new Set(activeRow.runs.map((r) => r.id));
    return lineage.delegations
      .filter((d) => mine.has(d.parent) || mine.has(d.child))
      .map((d) => {
        const a = placement.byKey.get(lineage.memberOf[d.parent]);
        const b = placement.byKey.get(lineage.memberOf[d.child]);
        return a && b && a !== b ? linkPath(a, b) : null;
      })
      .filter((p): p is string => p !== null);
  }, [activeRow, lineage, placement]);

  const header = (
    <PaneHeader
      title="Lineage"
      meta={[
        <span key="r">
          <RollingNumber value={runs.length} /> runs
        </span>,
        <span key="h">
          <RollingNumber value={harnessCount} /> harnesses
        </span>,
        <span key="a">
          <RollingNumber value={agentCount} /> agents
        </span>,
        <span key="f" style={failed ? { color: 'var(--danger)' } : undefined}>
          <RollingNumber value={failed} /> failed
        </span>,
      ]}
      actions={
        <Button size="sm" icon={<RefreshIcon />} onClick={() => void refresh()}>
          Refresh
        </Button>
      }
    />
  );

  return (
    <SectionShell header={header} error={error}>
      <div className="traj-lin">
        <div className="traj-lin-controls">
          <div className="traj-lin-seg" role="group" aria-label="Tree depth">
            <button
              type="button"
              className="traj-lin-seg-btn"
              aria-pressed={level === 'harnesses'}
              onClick={() => setLevel('harnesses')}
            >
              harnesses <span className="cnt">{harnessCount}</span>
            </button>
            <button
              type="button"
              className="traj-lin-seg-btn"
              aria-pressed={level === 'runs'}
              onClick={() => setLevel('runs')}
            >
              runs <span className="cnt">{runs.length}</span>
            </button>
          </div>
          <label className="traj-lin-spread">
            spread
            <input
              type="range"
              min={0.5}
              max={2.5}
              step={0.1}
              value={spread}
              onChange={(e) => setSpread(Number(e.target.value))}
            />
          </label>
          <span className="traj-lin-hint">
            {level === 'runs'
              ? 'every run · click a node to isolate its lineage'
              : 'one node per harness revision · size is run count'}
            {total > runs.length ? ` · newest ${runs.length} of ${total}` : ''}
          </span>
        </div>

        <div
          ref={stageRef}
          className="traj-lin-stage"
          // Focusable so Escape can clear a focus without a global key handler.
          tabIndex={0}
          onKeyDown={onKeyDown}
        >
          <svg
            ref={svgRef}
            role="img"
            aria-label={`Lineage of ${runs.length} runs across ${harnessCount} harnesses`}
            onClick={clear}
          >
            <g ref={viewRef}>
              <g>
                {placement.links.map((link) => (
                  <path
                    key={link.target.row.key}
                    className={`traj-lin-link${
                      lit && !(lit.has(link.source.row.key) && lit.has(link.target.row.key))
                        ? ' traj-lin-dim'
                        : ''
                    }`}
                    data-edge={link.edge}
                    d={linkPath(link.source, link.target)}
                  />
                ))}
              </g>
              <g>
                {delegationPaths.map((d, i) => (
                  <path key={i} className="traj-lin-xedge" d={d} />
                ))}
              </g>
              <g>
                {placement.nodes.map((node, i) => (
                  <LineageNode
                    key={node.row.key}
                    node={node}
                    level={level}
                    index={Math.min(i, STAGGER_CAP)}
                    hot={node.row.key === active}
                    dim={lit ? !lit.has(node.row.key) : false}
                    onEnter={(e) => showCard(node.row.key, e)}
                    onLeave={scheduleHide}
                    onClick={(e) => toggleFocus(node.row.key, e)}
                  />
                ))}
              </g>
            </g>
          </svg>

          {loading && runs.length === 0 ? (
            <div style={overlayCenter}>
              <Loading what="lineage" />
            </div>
          ) : !loading && runs.length === 0 ? (
            <div style={overlayCenter}>
              <EmptyState title="No runs to trace">
                Runs appear once an agent acts while a dataset captures. Check the Datasets section,
                or push runs in with the Python SDK.
              </EmptyState>
            </div>
          ) : null}

          <div className="traj-lin-corner traj-lin-hint" data-at="left">
            ring = graded · dashed = model swap
          </div>
          <div className="traj-lin-corner" data-at="right">
            <Button size="sm" intent="ghost" onClick={fit}>
              Reset view
            </Button>
          </div>

          {runs.length > 0 ? (
            <RunsInset
              runs={runs}
              lineage={lineage}
              metric={metric}
              onMetric={setMetric}
              active={active}
              lit={lit}
              onEnter={(runId, e) => showCard(lineage.memberOf[runId], e)}
              onLeave={scheduleHide}
              onClick={(runId, e) => toggleFocus(lineage.memberOf[runId], e)}
            />
          ) : null}

          {focus && rowByKey.get(focus) ? (
            <div className="traj-lin-focus">
              <span>isolating</span>
              <b>{rowTitle(rowByKey.get(focus)!)}</b>
              <Button size="sm" intent="ghost" onClick={clear}>
                Clear
              </Button>
            </div>
          ) : null}

          {card && rowByKey.get(card.key) ? (
            <LineageCard
              row={rowByKey.get(card.key)!}
              lineage={lineage}
              at={card}
              stage={size}
              onEnter={() => clearTimeout(hideTimer.current)}
              onLeave={scheduleHide}
              onOpenRun={onOpenRun}
              onInspectHarness={onInspectHarness}
            />
          ) : null}
        </div>

        <Legend />
      </div>
    </SectionShell>
  );
}

const overlayCenter: CSSProperties = {
  position: 'absolute',
  inset: 0,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  pointerEvents: 'none',
};

/** Leave the inset's corner clear when the stage is wide enough to have one. */
function insetReserve(width: number): number {
  return width > 640 ? 340 : 0;
}

function rowTitle(row: LineageRow): string {
  if (row.kind === 'run') return row.run?.goal || row.run?.id || 'run';
  if (row.kind === 'harness') return row.harness?.label || row.label;
  return row.label;
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function runRadius(run: TrajectoryRun): number {
  return Math.min(8, 2.6 + Math.sqrt(run.steps) * 1.1);
}

// --- one node -----------------------------------------------------------------------

function LineageNode({
  node,
  level,
  index,
  hot,
  dim,
  onEnter,
  onLeave,
  onClick,
}: {
  node: PlacedNode;
  level: Level;
  index: number;
  hot: boolean;
  dim: boolean;
  onEnter: (e: MouseEvent) => void;
  onLeave: () => void;
  onClick: (e: MouseEvent) => void;
}) {
  const { row } = node;
  const style = { '--traj-i': index } as CSSProperties;
  let body: ReactNode;
  let halo = 10;

  if (row.kind === 'agent') {
    const s = hot ? 8.5 : 7;
    body = (
      <>
        <path className="dot" d={`M0,${-s}L${s},0L0,${s}L${-s},0Z`} />
        <text className="label" x={-12} y={3.5} textAnchor="end">
          {truncate(row.label, 18)}
        </text>
      </>
    );
    halo = 16;
  } else if (row.kind === 'harness') {
    const r = level === 'harnesses' ? 6 + Math.sqrt(row.runs.length) * 2.4 : 5.5;
    const failing = row.runs.some((run) => verdictOf(run) === 'failure');
    halo = r * 2.6;
    body = (
      <>
        <circle className="dot" r={hot ? r * 1.25 : r} />
        {level === 'harnesses' ? (
          <text className="count" textAnchor="middle" y={3}>
            {row.runs.length}
          </text>
        ) : null}
        <text className="label" x={r + 4} y={-r - 2}>
          {truncate(row.label, 15)}
        </text>
      </>
    );
    return (
      <g
        className={`traj-lin-node${dim ? ' traj-lin-dim' : ''}`}
        data-kind="harness"
        data-hot={hot}
        data-failing={failing}
        transform={`translate(${node.x},${node.y})`}
        style={style}
        onMouseEnter={onEnter}
        onMouseLeave={onLeave}
        onClick={onClick}
      >
        <circle className="halo" r={hot ? halo : 0} />
        {body}
      </g>
    );
  } else {
    const run = row.run!;
    const r = runRadius(run);
    const verdict = verdictOf(run);
    halo = Math.min(28, r * 4);
    body = (
      <>
        <circle className="dot" data-verdict={verdict} r={hot ? r * 1.5 : r} />
        {isGraded(run) ? <circle className="ring" r={r + 3} /> : null}
        {verdict === 'running' ? <circle className="traj-lin-live" r={r + 4} /> : null}
      </>
    );
  }

  return (
    <g
      className={`traj-lin-node${dim ? ' traj-lin-dim' : ''}`}
      data-kind={row.kind}
      data-hot={hot}
      transform={`translate(${node.x},${node.y})`}
      style={style}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onClick={onClick}
    >
      <circle className="halo" r={hot ? halo : 0} />
      {body}
    </g>
  );
}

// --- the inset ---------------------------------------------------------------------

const IW = 318;
const IH = 152;
const IM = { t: 8, r: 10, b: 16, l: 34 };

const METRIC_LABEL: Record<YMetric, string> = {
  steps: 'steps',
  duration: 'time',
  tokens: 'tok out',
};

function formatTick(value: number, metric: YMetric): string {
  if (metric === 'duration') {
    if (value < 1000) return `${value}ms`;
    if (value < 60_000 * 10) return `${value / 1000}s`;
    return `${Math.round(value / 60_000)}m`;
  }
  if (value >= 1000) return `${value / 1000}k`;
  return String(value);
}

function RunsInset({
  runs,
  lineage,
  metric,
  onMetric,
  active,
  lit,
  onEnter,
  onLeave,
  onClick,
}: {
  runs: TrajectoryRun[];
  lineage: Lineage;
  metric: YMetric;
  onMetric: (m: YMetric) => void;
  active: string | null;
  lit: Set<string> | null;
  onEnter: (runId: string, e: MouseEvent) => void;
  onLeave: () => void;
  onClick: (runId: string, e: MouseEvent) => void;
}) {
  const plotted = useMemo(
    () =>
      runs
        .map((run) => ({ run, v: metricValue(run, metric) }))
        .filter((p): p is { run: TrajectoryRun; v: number } => p.v != null),
    [runs, metric],
  );
  const missing = runs.length - plotted.length;

  const t0 = Math.min(...runs.map((r) => r.started_at));
  const t1 = Math.max(...runs.map((r) => r.started_at));
  const span = t1 - t0 || 1;
  const x = (t: number) => IM.l + ((t - t0) / span) * (IW - IM.l - IM.r);
  const y = yAxis(
    plotted.map((p) => p.v),
    METRIC_IS_LOG[metric],
    IH - IM.b,
    IM.t,
  );
  const xTicks = [0, 1 / 3, 2 / 3, 1].map((f) => t0 + f * span);

  return (
    <div className="traj-lin-inset" onClick={(e) => e.stopPropagation()}>
      <div className="traj-lin-inset-head">
        <span>Runs over time</span>
        {missing > 0 ? (
          <span className="traj-lin-hint" title={`${missing} runs never reported this figure`}>
            {missing} n/a
          </span>
        ) : null}
        <span className="spacer" />
        {(Object.keys(METRIC_LABEL) as YMetric[]).map((m) => (
          <button
            key={m}
            type="button"
            className="traj-lin-metric"
            aria-pressed={metric === m}
            onClick={() => onMetric(m)}
          >
            {METRIC_LABEL[m]}
          </button>
        ))}
      </div>
      <svg viewBox={`0 0 ${IW} ${IH}`} preserveAspectRatio="xMidYMid meet">
        {y.ticks.map((v) => (
          <g key={v}>
            <line className="traj-lin-grid" x1={IM.l} x2={IW - IM.r} y1={y.map(v)} y2={y.map(v)} />
            <text className="traj-lin-axis" x={IM.l - 4} y={y.map(v) + 3} textAnchor="end">
              {formatTick(v, metric)}
            </text>
          </g>
        ))}
        {xTicks.map((t) => {
          const d = new Date(t * 1000);
          return (
            <text key={t} className="traj-lin-axis" x={x(t)} y={IH - 4} textAnchor="middle">
              {`${d.getMonth() + 1}/${d.getDate()}`}
            </text>
          );
        })}
        {/* Harness revisions: when the configuration changed, beside the runs that
            followed. Origins are skipped — an agent's first harness is not a change. */}
        {lineage.revisions
          .filter((rev) => rev.edge !== 'origin')
          .map((rev) => (
            <line
              key={rev.key}
              className="traj-lin-rev"
              data-edge={rev.edge}
              x1={x(rev.t)}
              x2={x(rev.t)}
              y1={IM.t}
              y2={IH - IM.b}
            />
          ))}
        {plotted.map(({ run, v }) => {
          const key = lineage.memberOf[run.id];
          const hot = key === active;
          const dim = lit ? !lit.has(key) : false;
          return (
            <circle
              key={run.id}
              className="traj-lin-pt"
              data-verdict={verdictOf(run)}
              data-hot={hot}
              cx={x(run.started_at)}
              cy={y.map(v)}
              r={hot ? 4.5 : 3}
              opacity={dim ? 0.15 : 1}
              onMouseEnter={(e) => onEnter(run.id, e)}
              onMouseLeave={onLeave}
              onClick={(e) => onClick(run.id, e)}
            />
          );
        })}
      </svg>
    </div>
  );
}

// --- the detail card --------------------------------------------------------------

function LineageCard({
  row,
  lineage,
  at,
  stage,
  onEnter,
  onLeave,
  onOpenRun,
  onInspectHarness,
}: {
  row: LineageRow;
  lineage: Lineage;
  at: CardAt;
  stage: { w: number; h: number };
  onEnter: () => void;
  onLeave: () => void;
  onOpenRun: (runId: string) => void;
  onInspectHarness: (fingerprint: string) => void;
}) {
  const width = 300;
  const left = at.x + 16 + width > stage.w ? Math.max(8, at.x - 16 - width) : at.x + 16;
  const top = Math.max(8, Math.min(at.y + 12, stage.h - 280));
  const failures = row.runs.filter((r) => verdictOf(r) === 'failure').length;

  let content: ReactNode;
  let verdict: Verdict | undefined;

  if (row.kind === 'run' && row.run) {
    const run = row.run;
    verdict = verdictOf(run);
    const tok = tokens(run.tokens_in, run.tokens_out);
    const cost = usd(run.cost_usd);
    content = (
      <>
        <div className="traj-lin-card-kicker">Run · {ago(run.started_at)}</div>
        <div className="traj-lin-card-title">{run.goal || '(no goal)'}</div>
        <div className="traj-lin-card-big">
          <span className="n" data-tone={verdict === 'failure' ? 'fail' : undefined}>
            <RollingNumber value={run.steps} />
          </span>
          steps · {run.rounds} rounds
          <span style={{ marginLeft: 'auto' }}>
            <Chip kind={VERDICT_KIND[verdict]}>{verdict}</Chip>
          </span>
        </div>
        <div className="traj-lin-card-rows">
          <span className="k">agent</span>
          <span className="v">{row.agent}</span>
          <span className="k">model</span>
          <span className="v">{shortModel(run.model)}</span>
          <span className="k">time</span>
          <span className="v">{ms(run.duration_ms)}</span>
          {tok ? (
            <>
              <span className="k">tokens</span>
              <span className="v">{tok}</span>
            </>
          ) : null}
          {cost ? (
            <>
              <span className="k">cost</span>
              <span className="v">{cost}</span>
            </>
          ) : null}
          <span className="k">dataset</span>
          <span className="v">{run.dataset_id}</span>
        </div>
        {run.error ? <div className="traj-lin-card-error">{truncate(run.error, 220)}</div> : null}
        <div className="traj-lin-card-actions">
          <Button size="sm" intent="primary" onClick={() => onOpenRun(run.id)}>
            Open run
          </Button>
          {run.harness ? (
            <Button size="sm" intent="ghost" onClick={() => onInspectHarness(run.harness!)}>
              Harness
            </Button>
          ) : null}
        </div>
      </>
    );
  } else if (row.kind === 'harness' && row.harness) {
    const harness = row.harness;
    const chain = lineage.rows.filter((r) => r.kind === 'harness' && r.agent === row.agent);
    const rev = chain.findIndex((r) => r.key === row.key) + 1;
    const real = !harness.fingerprint.startsWith('none:');
    content = (
      <>
        <div className="traj-lin-card-kicker">
          Harness · rev {rev} of {chain.length}
        </div>
        <div className="traj-lin-card-title">{harness.label || row.label}</div>
        <div className="traj-lin-card-big">
          <span className="n">
            <RollingNumber value={row.runs.length} />
          </span>
          runs
          {failures ? (
            <>
              {' · '}
              <span className="n" data-tone="fail">
                <RollingNumber value={failures} />
              </span>
              failed
            </>
          ) : null}
        </div>
        <div className="traj-lin-card-rows">
          <span className="k">print</span>
          <span className="v">{real ? harness.fingerprint.slice(0, 16) : 'unfingerprinted'}</span>
          <span className="k">model</span>
          <span className="v">{harness.model || '—'}</span>
          <span className="k">tools</span>
          <span className="v">{harness.tool_names.length}</span>
          <span className="k">first run</span>
          <span className="v">{ago(row.runs[0].started_at)}</span>
        </div>
        {row.changes.length ? (
          <>
            <div className="traj-lin-card-sec">Changed from rev {rev - 1}</div>
            <ul className="traj-lin-card-changes">
              {row.changes.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </>
        ) : rev > 1 ? (
          <>
            <div className="traj-lin-card-sec">Changed from rev {rev - 1}</div>
            <ul className="traj-lin-card-changes">
              <li>tool schemas only</li>
            </ul>
          </>
        ) : null}
        {real ? (
          <div className="traj-lin-card-actions">
            <Button
              size="sm"
              intent="primary"
              onClick={() => onInspectHarness(harness.fingerprint)}
            >
              Inspect harness
            </Button>
          </div>
        ) : null}
      </>
    );
  } else {
    const models = [...new Set(row.runs.map((r) => shortModel(r.model)))];
    const harnessCount = lineage.rows.filter(
      (r) => r.kind === 'harness' && r.agent === row.agent,
    ).length;
    const last = row.runs[row.runs.length - 1];
    content = (
      <>
        <div className="traj-lin-card-kicker">Agent</div>
        <div className="traj-lin-card-title">{row.label}</div>
        <div className="traj-lin-card-big">
          <span className="n">
            <RollingNumber value={row.runs.length} />
          </span>
          runs · {harnessCount} harnesses
        </div>
        <div className="traj-lin-card-rows">
          <span className="k">failed</span>
          <span className="v">{failures}</span>
          <span className="k">last run</span>
          <span className="v">{last ? ago(last.started_at) : '—'}</span>
          <span className="k">models</span>
          <span className="v">{models.join(', ')}</span>
        </div>
      </>
    );
  }

  return (
    <div
      className="traj-lin-card"
      data-verdict={verdict}
      style={{ left, top, width }}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onClick={(e) => e.stopPropagation()}
    >
      {content}
    </div>
  );
}

// --- legend ------------------------------------------------------------------------

function Legend() {
  const dot = (verdict: Verdict) => (
    <svg width="10" height="10" viewBox="-5 -5 10 10" aria-hidden>
      <circle r="4" data-verdict={verdict} />
    </svg>
  );
  const line = (edge: string) => (
    <svg width="20" height="6" viewBox="0 -3 20 6" aria-hidden>
      <path className="traj-lin-link" data-edge={edge} d="M0,0H20" />
    </svg>
  );
  return (
    <div className="traj-lin-legend">
      <span className="it">{dot('success')} success</span>
      <span className="it">{dot('ungraded')} ungraded</span>
      <span className="it">{dot('failure')} failed</span>
      <span className="it">{dot('running')} running</span>
      <span className="it">
        <svg width="12" height="12" viewBox="-6 -6 12 12" aria-hidden>
          <circle className="traj-lin-legend-ring" r="5" />
        </svg>
        graded
      </span>
      <span className="it">{line('prompt')} prompt edit</span>
      <span className="it">{line('tools')} tools</span>
      <span className="it">{line('params')} params</span>
      <span className="it">{line('model')} model swap</span>
      <span className="it end">scroll to zoom · drag to pan · esc to clear</span>
    </div>
  );
}
