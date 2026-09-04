import { chartColors } from '../../../viz/uplot-theme';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';

import { revealSection } from '../../../layout/controller';
import { onTrainingEvent, watchRun, type TrainingEventMap } from '../client';

const dim = { color: 'var(--text-dim)' } as const;

type MetricPoint = TrainingEventMap['metrics'];

interface Series {
  xs: number[];
  ys: number[];
}

interface RunData {
  // metric name → aligned x/y arrays (x = step, falling back to wall clock).
  metrics: Map<string, Series>;
  name: string;
  lastTs: number;
}

const runs = new Map<string, RunData>();
const runListeners = new Set<() => void>();
let wired = false;

function ensureRun(runId: string, name?: string): RunData {
  let run = runs.get(runId);
  if (!run) {
    run = { metrics: new Map(), name: name ?? runId, lastTs: 0 };
    runs.set(runId, run);
    runListeners.forEach((l) => l());
  }
  return run;
}

function ingest(point: MetricPoint): void {
  const runId = point.runId ?? point.projectId;
  const run = ensureRun(runId);
  run.lastTs = point.ts;
  const x = point.step ?? point.ts;
  for (const [name, value] of Object.entries(point.values ?? {})) {
    let series = run.metrics.get(name);
    if (!series) {
      series = { xs: [], ys: [] };
      run.metrics.set(name, series);
      runListeners.forEach((l) => l());
    }
    series.xs.push(x);
    series.ys.push(value);
  }
}

function wire(): void {
  if (wired) return;
  wired = true;
  onTrainingEvent('metrics', ingest);
  onTrainingEvent('run_started', (d) => ensureRun(d.runId, d.name));
  onTrainingEvent('run_backfill', (d) => {
    d.runs.forEach((r) => ensureRun(r));
    if (d.points.length) {
      const run = ensureRun(d.runId);
      run.metrics.clear(); // replace with the authoritative backfill
      d.points.forEach(ingest);
    }
  });
}

/** One streaming uPlot chart for a single metric. */
function MetricChart({
  title,
  series,
  version,
}: {
  title: string;
  series: Series;
  version: number;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const colors = chartColors(host);
    const plot = new uPlot(
      {
        width: host.clientWidth || 420,
        height: 160,
        title,
        series: [
          {},
          {
            label: title,
            // Resolved, not `var(--accent)`: uPlot draws to a canvas, and
            // `ctx.strokeStyle` ignores a custom property silently — which is why
            // this line used to render in uPlot's default colour on every theme.
            stroke: colors.accent,
            width: 1.5,
            points: { show: false },
          },
        ],
        axes: [
          { stroke: colors.axis, grid: { stroke: colors.grid } },
          { stroke: colors.axis, grid: { stroke: colors.grid } },
        ],
        scales: { x: { time: false } },
        legend: { show: false },
        cursor: { drag: { x: true, y: false } },
      },
      [series.xs, series.ys],
      host,
    );
    plotRef.current = plot;
    const resize = new ResizeObserver(() => {
      plot.setSize({ width: host.clientWidth || 420, height: 160 });
    });
    resize.observe(host);
    return () => {
      resize.disconnect();
      plotRef.current = null;
      plot.destroy();
    };
    // Recreate only per metric; data streams through setData below.
  }, [title]);

  useEffect(() => {
    plotRef.current?.setData([series.xs, series.ys]);
  }, [series, version]);

  return <div ref={hostRef} />;
}

/**
 * Live training metrics: one streaming chart per metric of the selected run,
 * fed by `metrics` events (ring-buffer backfill via `watch_run` when opened
 * mid-run). Singleton widget.
 */
export function MetricsPane() {
  const [, force] = useState(0);
  const [runId, setRunId] = useState<string | null>(null);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    wire();
    // rAF-batch repaints: metric events can arrive far faster than 60 Hz.
    const bump = () => {
      if (frame.current != null) return;
      frame.current = requestAnimationFrame(() => {
        frame.current = null;
        force((v) => v + 1);
      });
    };
    runListeners.add(bump);
    const unsub = onTrainingEvent('metrics', bump);
    watchRun(''); // empty watch returns the known-run list for the selector
    return () => {
      runListeners.delete(bump);
      unsub();
      if (frame.current != null) cancelAnimationFrame(frame.current);
    };
  }, []);

  const runIds = [...runs.keys()];
  const active = runId ?? runIds[runIds.length - 1] ?? null;
  const run = active ? runs.get(active) : undefined;
  const version = useRef(0);
  version.current += 1;

  const select = useCallback((id: string) => {
    setRunId(id);
    watchRun(id); // backfill anything we missed
  }, []);

  const charts = useMemo(() => (run ? [...run.metrics.entries()] : []), [run, version.current]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'auto' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.25rem 0.5rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.75rem',
        }}
      >
        <span style={dim}>Run</span>
        <select value={active ?? ''} onChange={(e) => select(e.target.value)}>
          {runIds.length === 0 && <option value="">(none yet)</option>}
          {runIds.map((id) => (
            <option key={id} value={id}>
              {runs.get(id)?.name ?? id}
            </option>
          ))}
        </select>
      </div>
      <div style={{ flex: 1, padding: '0.5rem' }}>
        {/*
          Two different facts, which one paragraph used to conflate: nothing has
          ever reported to this node, versus a run is selected and simply hasn't
          logged a scalar yet. The first is answered by starting a run, the second
          by adding a `log` call — telling someone with no projects at all to add
          a line to a training loop they don't have is the dead end here.
        */}
        {charts.length === 0 &&
          (runIds.length === 0 ? (
            <div style={{ fontSize: '0.8rem', display: 'grid', gap: '0.5rem', ...dim }}>
              <div>No training runs have reported to this node yet.</div>
              <div>
                <button onClick={() => revealSection('projects', 'explorer.home')}>
                  Browse training projects
                </button>
              </div>
              <div>
                Once a run calls <code>horrible_train.log(step=i, loss=…)</code>, its curves stream
                here live.
              </div>
            </div>
          ) : (
            <div style={{ fontSize: '0.8rem', ...dim }}>
              <strong>{run?.name ?? active}</strong> hasn&apos;t logged a scalar yet — call{' '}
              <code>horrible_train.log(step=i, loss=…)</code> in its training loop and curves stream
              here live.
            </div>
          ))}
        {charts.map(([name, series]) => (
          <MetricChart
            key={`${active}:${name}`}
            title={name}
            series={series}
            version={version.current}
          />
        ))}
      </div>
    </div>
  );
}
