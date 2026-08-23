import { useEffect, useMemo, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { queryMetrics } from '../api';
import { getRunColor, useLocalTrackStore } from '../store';
import type { MetricSeries, PanelConfig } from '../types';

export function ChartPanel({
  panel,
  onRemove,
}: {
  panel: PanelConfig;
  onRemove?: () => void;
}) {
  const { metricRevisions, runs, selectedRunIds, globalSmoothing } = useLocalTrackStore();
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);

  const [seriesData, setSeriesData] = useState<MetricSeries[]>([]);
  const [loading, setLoading] = useState(false);
  const [logScale, setLogScale] = useState(panel.scale === 'log');

  const selectedRunArray = useMemo(() => {
    return runs.filter((r) => selectedRunIds.has(r.id));
  }, [runs, selectedRunIds]);

  // Fetch metric data when selected runs, metricKey, or globalSmoothing changes
  useEffect(() => {
    if (selectedRunArray.length === 0) {
      setSeriesData([]);
      return;
    }

    let isMounted = true;
    setLoading(true);

    const runIds = selectedRunArray.map((r) => r.id);
    const smoothingFactor = panel.smoothing ?? globalSmoothing;

    queryMetrics(runIds, [panel.metricKey], 600, smoothingFactor)
      .then((res) => {
        if (isMounted) {
          setSeriesData(res);
          setLoading(false);
        }
      })
      .catch(() => {
        if (isMounted) setLoading(false);
      });

    return () => {
      isMounted = false;
    };
    // The live signal, scoped to THIS panel's metric: the backend said a selected
    // run has new points for this key, so the series is refetched. Reading the
    // whole revisions object here instead would refetch every panel on every
    // point of every metric.
  }, [
    selectedRunArray,
    panel.metricKey,
    panel.smoothing,
    globalSmoothing,
    metricRevisions[panel.metricKey],
  ]);

  // Build aligned uPlot 2D data array [xs, ys_1, ys_2, ...]
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    if (seriesData.length === 0) {
      if (plotRef.current) {
        plotRef.current.destroy();
        plotRef.current = null;
      }
      return;
    }

    // 1. Collect all distinct step points across all active series
    const stepSet = new Set<number>();
    seriesData.forEach((s) => {
      s.steps.forEach((st) => stepSet.add(st));
    });
    const alignedSteps = Array.from(stepSet).sort((a, b) => a - b);

    if (alignedSteps.length === 0) return;

    // 2. Build series y-arrays aligned with alignedSteps
    // `AlignedData` is a readonly tuple type, so it has no `push`. The series are
    // accumulated as a plain array of the same element type and handed over once,
    // which is what the old `as any` was papering over.
    const uplotData: (number | null)[][] = [alignedSteps];
    const uplotSeries: uPlot.Series[] = [
      {
        label: 'Step',
        value: (_u, v) => (v != null ? `${v}` : '--'),
      },
    ];

    seriesData.forEach((s, idx) => {
      const run = runs.find((r) => r.id === s.run_id);
      const runName = run?.name ?? s.run_id;
      const color = getRunColor(s.run_id, idx);

      const stepToVal = new Map<number, number>();
      s.steps.forEach((st, i) => {
        stepToVal.set(st, s.values[i]);
      });

      // Align points (null for missing steps)
      const alignedYs = alignedSteps.map((st) => stepToVal.get(st) ?? null);
      uplotData.push(alignedYs);

      uplotSeries.push({
        label: runName,
        stroke: color,
        width: 1.75,
        points: { show: s.steps.length < 30, size: 4 },
        spanGaps: true,
        value: (_u, v) => (v != null ? (v < 0.01 ? v.toExponential(3) : v.toFixed(4)) : '--'),
      });
    });

    const opts: uPlot.Options = {
      width: host.clientWidth || 400,
      height: 200,
      scales: {
        x: { time: false },
        y: {
          distr: logScale ? 3 : 1, // 3 = log, 1 = linear
          auto: true,
        },
      },
      series: uplotSeries,
      axes: [
        {
          stroke: '#8b949e',
          grid: { stroke: 'rgba(255,255,255,0.06)' },
          ticks: { stroke: '#30363d' },
          size: 26,
        },
        {
          stroke: '#8b949e',
          grid: { stroke: 'rgba(255,255,255,0.06)' },
          ticks: { stroke: '#30363d' },
          size: 42,
        },
      ],
      cursor: {
        drag: { setScale: true },
        points: { size: 6 },
      },
      legend: { show: false }, // We render a custom modern legend
    };

    if (plotRef.current) {
      plotRef.current.destroy();
    }

    plotRef.current = new uPlot(opts, uplotData as unknown as uPlot.AlignedData, host);

    // Responsive resize handler
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (plotRef.current && entry.contentRect.width > 0) {
          plotRef.current.setSize({
            width: entry.contentRect.width,
            height: Math.max(160, entry.contentRect.height - 40),
          });
        }
      }
    });
    ro.observe(host);

    return () => {
      ro.disconnect();
      if (plotRef.current) {
        plotRef.current.destroy();
        plotRef.current = null;
      }
    };
  }, [seriesData, logScale, runs]);

  return (
    <div
      style={{
        background: 'var(--bg-secondary, #161b22)',
        border: '1px solid var(--border-dim, #30363d)',
        borderRadius: 8,
        padding: '10px 14px',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 280,
        boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
      }}
    >
      {/* Panel Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 8,
          borderBottom: '1px solid rgba(255,255,255,0.05)',
          paddingBottom: 6,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, overflow: 'hidden' }}>
          <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary, #c9d1d9)', whiteSpace: 'nowrap' }}>
            {panel.title}
          </span>
          <span style={{ fontSize: 11, color: 'var(--text-dim, #8b949e)', background: 'rgba(255,255,255,0.04)', padding: '1px 5px', borderRadius: 3 }}>
            {panel.metricKey}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {/* Linear / Log Toggle */}
          <button
            onClick={() => setLogScale(!logScale)}
            title={logScale ? 'Switch to Linear Scale' : 'Switch to Log Scale'}
            style={{
              background: logScale ? 'rgba(56, 139, 253, 0.2)' : 'rgba(255,255,255,0.04)',
              border: '1px solid var(--border-dim, #30363d)',
              color: logScale ? 'var(--accent, #58a6ff)' : 'var(--text-dim, #8b949e)',
              fontSize: 10,
              fontWeight: 600,
              padding: '2px 5px',
              borderRadius: 3,
              cursor: 'pointer',
            }}
          >
            {logScale ? 'LOG' : 'LIN'}
          </button>

          {/* Remove Panel */}
          {onRemove && (
            <button
              onClick={onRemove}
              title="Remove panel"
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--text-dim, #8b949e)',
                fontSize: 14,
                cursor: 'pointer',
                padding: '0 4px',
              }}
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Chart Host Container */}
      <div
        ref={hostRef}
        style={{
          flex: 1,
          position: 'relative',
          width: '100%',
          minHeight: 180,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {loading && seriesData.length === 0 && (
          <div style={{ color: 'var(--text-dim, #8b949e)', fontSize: 12 }}>
            Loading metric series...
          </div>
        )}
        {!loading && seriesData.length === 0 && (
          <div style={{ color: 'var(--text-dim, #8b949e)', fontSize: 12 }}>
            {selectedRunArray.length === 0 ? 'Select runs to display metric' : `No '${panel.metricKey}' data logged yet`}
          </div>
        )}
      </div>

      {/* Custom Modern Run Legend */}
      {seriesData.length > 0 && (
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 8,
            marginTop: 6,
            paddingTop: 6,
            borderTop: '1px solid rgba(255,255,255,0.05)',
            maxHeight: 48,
            overflowY: 'auto',
          }}
        >
          {seriesData.map((s, idx) => {
            const run = runs.find((r) => r.id === s.run_id);
            const color = getRunColor(s.run_id, idx);
            const latestVal = s.values[s.values.length - 1];

            return (
              <div
                key={s.run_id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  fontSize: 11,
                  background: 'rgba(255,255,255,0.03)',
                  padding: '2px 6px',
                  borderRadius: 4,
                }}
              >
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: color }} />
                <span style={{ color: 'var(--text-secondary, #8b949e)', fontWeight: 500 }}>
                  {run?.name ?? s.run_id}:
                </span>
                <span style={{ color: 'var(--text-primary, #c9d1d9)', fontWeight: 600 }}>
                  {latestVal != null
                    ? latestVal < 0.01
                      ? latestVal.toExponential(3)
                      : latestVal.toFixed(4)
                    : '--'}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
