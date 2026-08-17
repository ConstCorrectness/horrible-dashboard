import { getRunColor, useLocalTrackStore } from '../store';
import type { PanelConfig } from '../types';

export function BarPanel({
  panel,
  onRemove,
}: {
  panel: PanelConfig;
  onRemove?: () => void;
}) {
  const { runs, selectedRunIds } = useLocalTrackStore();

  const selectedRuns = runs.filter((r) => selectedRunIds.has(r.id));
  const values = selectedRuns.map((r) => r.summary[panel.metricKey] ?? 0);
  const maxVal = Math.max(...values, 0.00001);

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
        minHeight: 220,
        boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 10,
          borderBottom: '1px solid rgba(255,255,255,0.05)',
          paddingBottom: 6,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary, #c9d1d9)' }}>
            {panel.title} (Bar Chart)
          </span>
          <span style={{ fontSize: 10, color: 'var(--text-dim, #8b949e)', background: 'rgba(255,255,255,0.04)', padding: '1px 4px', borderRadius: 3 }}>
            {panel.metricKey}
          </span>
        </div>
        {onRemove && (
          <button
            onClick={onRemove}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-dim, #8b949e)',
              cursor: 'pointer',
            }}
          >
            ✕
          </button>
        )}
      </div>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 10, justifyContent: 'center', overflowY: 'auto' }}>
        {selectedRuns.length === 0 ? (
          <div style={{ color: 'var(--text-dim, #8b949e)', fontSize: 12, margin: 'auto' }}>
            No runs selected
          </div>
        ) : (
          selectedRuns.map((run, idx) => {
            const val = run.summary[panel.metricKey];
            const color = getRunColor(run.id, idx);
            const numVal = typeof val === 'number' ? val : 0;
            const pct = Math.min(100, Math.max(2, (numVal / maxVal) * 100));

            return (
              <div key={run.id} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                  <span style={{ color: 'var(--text-secondary, #8b949e)', fontWeight: 500 }}>
                    {run.name}
                  </span>
                  <span style={{ color: 'var(--text-primary, #c9d1d9)', fontWeight: 600, fontFamily: 'monospace' }}>
                    {val != null ? (typeof val === 'number' ? val.toFixed(4) : val) : '--'}
                  </span>
                </div>
                <div
                  style={{
                    height: 12,
                    background: 'rgba(255,255,255,0.05)',
                    borderRadius: 4,
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      height: '100%',
                      width: `${pct}%`,
                      background: color,
                      borderRadius: 4,
                      transition: 'width 0.3s ease',
                    }}
                  />
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
