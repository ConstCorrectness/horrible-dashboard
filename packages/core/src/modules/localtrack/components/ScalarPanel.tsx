import { getRunColor, useLocalTrackStore } from '../store';
import type { PanelConfig } from '../types';

export function ScalarPanel({
  panel,
  onRemove,
}: {
  panel: PanelConfig;
  onRemove?: () => void;
}) {
  const { runs, selectedRunIds } = useLocalTrackStore();

  const selectedRuns = runs.filter((r) => selectedRunIds.has(r.id));

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
        minHeight: 200,
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
            {panel.title}
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

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto' }}>
        {selectedRuns.length === 0 ? (
          <div style={{ color: 'var(--text-dim, #8b949e)', fontSize: 12, margin: 'auto' }}>
            No runs selected
          </div>
        ) : (
          selectedRuns.map((run, idx) => {
            const val = run.summary[panel.metricKey];
            const color = getRunColor(run.id, idx);

            return (
              <div
                key={run.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '8px 12px',
                  borderRadius: 6,
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.04)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 10, height: 10, borderRadius: '50%', background: color }} />
                  <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-primary, #c9d1d9)' }}>
                    {run.name}
                  </span>
                </div>

                <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary, #c9d1d9)', fontFamily: 'monospace' }}>
                  {val != null
                    ? typeof val === 'number'
                      ? val < 0.01
                        ? val.toExponential(3)
                        : val.toFixed(4)
                      : val
                    : '--'}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
