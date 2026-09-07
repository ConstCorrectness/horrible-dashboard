import { useMemo } from 'react';

import { useLocalTrackStore } from '../store';
import type { PanelConfig } from '../types';
import { ComparePanel } from './ComparePanel';
import { ParcoordsPanel } from './ParcoordsPanel';

/**
 * The workspace-grid wrappers for the two comparison panel types.
 *
 * They differ from the chart panels in what they read: a chart panel asks for a
 * metric *series*, these ask for each run's **config** and its final metric. That
 * is why they are panel types rather than a mode on `ChartPanel` — the data, the
 * question and the failure mode ("nothing varied") are all different.
 *
 * Both take their runs from the same sidebar selection every other panel uses, so
 * ticking a run adds it to the curves and to the table at once.
 */

function Frame({
  title,
  subtitle,
  onRemove,
  children,
}: {
  title: string;
  subtitle?: string;
  onRemove?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-lg)',
        padding: '10px 14px',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 220,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 10,
          borderBottom: '1px solid var(--border)',
          paddingBottom: 6,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary)' }}>
            {title}
          </span>
          {subtitle && (
            <span
              style={{
                fontSize: 10,
                color: 'var(--text-dim)',
                fontFamily: 'var(--font-mono, monospace)',
              }}
            >
              {subtitle}
            </span>
          )}
        </div>
        {onRemove && (
          <button
            onClick={onRemove}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-dim)',
              cursor: 'pointer',
            }}
          >
            ✕
          </button>
        )}
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>{children}</div>
    </div>
  );
}

function useSelectedRunIds(): string[] {
  const { runs, selectedRunIds } = useLocalTrackStore();
  // Derived from `runs` rather than from the Set directly, so the order matches
  // the sidebar and a re-render with the same selection is a stable array.
  return useMemo(
    () => runs.filter((r) => selectedRunIds.has(r.id)).map((r) => r.id),
    [runs, selectedRunIds],
  );
}

export function CompareGridPanel({
  panel,
  onRemove,
}: {
  panel: PanelConfig;
  onRemove?: () => void;
}) {
  const runIds = useSelectedRunIds();
  return (
    <Frame
      title={panel.title || 'Comparison'}
      subtitle={panel.metricKey || 'all metrics'}
      onRemove={onRemove}
    >
      <ComparePanel runIds={runIds} metricKey={panel.metricKey} />
    </Frame>
  );
}

export function ParcoordsGridPanel({
  panel,
  onRemove,
}: {
  panel: PanelConfig;
  onRemove?: () => void;
}) {
  const runIds = useSelectedRunIds();
  return (
    <Frame
      title={panel.title || 'Parallel coordinates'}
      subtitle={panel.metricKey}
      onRemove={onRemove}
    >
      <ParcoordsPanel runIds={runIds} metricKey={panel.metricKey} />
    </Frame>
  );
}
