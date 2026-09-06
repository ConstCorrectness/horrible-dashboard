import { useState } from 'react';
import { useLocalTrackStore } from '../store';
import type { ChartType, YAxisScale } from '../types';

export function AddPanelModal({
  isOpen,
  onClose,
}: {
  isOpen: boolean;
  onClose: () => void;
}) {
  const { discoveredMetrics, addPanel } = useLocalTrackStore();

  const [metricKey, setMetricKey] = useState(discoveredMetrics[0] ?? 'train/loss');
  const [customKey, setCustomKey] = useState('');
  const [chartType, setChartType] = useState<ChartType>('line');
  const [title, setTitle] = useState('');
  const [scale, setScale] = useState<YAxisScale>('linear');

  if (!isOpen) return null;

  const activeKey = customKey.trim() ? customKey.trim() : metricKey;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeKey) return;

    addPanel({
      title: title.trim() || activeKey,
      metricKey: activeKey,
      chartType,
      scale,
      colSpan: chartType === 'scalar' ? 1 : 1,
    });

    setTitle('');
    setCustomKey('');
    onClose();
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.65)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9999,
        backdropFilter: 'blur(2px)',
      }}
    >
      <form
        onSubmit={handleSubmit}
        style={{
          background: 'var(--bg-primary, #0d1117)',
          border: '1px solid var(--border-dim, #30363d)',
          borderRadius: 8,
          padding: 24,
          width: 440,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
          boxShadow: '0 12px 36px rgba(0,0,0,0.6)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary, #c9d1d9)' }}>
            + Add Metric Panel
          </span>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-dim, #8b949e)',
              fontSize: 16,
              cursor: 'pointer',
            }}
          >
            ✕
          </button>
        </div>

        {/* Metric Selection */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-secondary, #8b949e)' }}>
            Discovered Metrics
          </label>
          <select
            value={metricKey}
            onChange={(e) => setMetricKey(e.target.value)}
            style={{
              background: 'var(--bg-secondary, #161b22)',
              color: 'var(--text-primary, #c9d1d9)',
              border: '1px solid var(--border-dim, #30363d)',
              borderRadius: 4,
              // Horizontal padding only: `controls.css` fixes this control's height and
              // strips its vertical padding (the One Height Rule) — see theming.mdx.
              padding: '0 8px',
              fontSize: 13,
              outline: 'none',
            }}
          >
            {discoveredMetrics.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
            {discoveredMetrics.length === 0 && (
              <>
                <option value="train/loss">train/loss</option>
                <option value="eval/loss">eval/loss</option>
                <option value="eval/accuracy">eval/accuracy</option>
                <option value="train/learning_rate">train/learning_rate</option>
                <option value="train/grad_norm">train/grad_norm</option>
              </>
            )}
          </select>
          <input
            type="text"
            placeholder="Or type custom metric key..."
            value={customKey}
            onChange={(e) => setCustomKey(e.target.value)}
            style={{
              background: 'var(--bg-secondary, #161b22)',
              color: 'var(--text-primary, #c9d1d9)',
              border: '1px solid var(--border-dim, #30363d)',
              borderRadius: 4,
              padding: '0 8px',
              fontSize: 12,
              outline: 'none',
            }}
          />
        </div>

        {/* Chart Type */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-secondary, #8b949e)' }}>
            Chart Type
          </label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
            {(['line', 'bar', 'scalar'] as ChartType[]).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => setChartType(type)}
                style={{
                  background: chartType === type ? 'rgba(56, 139, 253, 0.2)' : 'var(--bg-secondary, #161b22)',
                  border: `1px solid ${chartType === type ? 'var(--accent, #58a6ff)' : 'var(--border-dim, #30363d)'}`,
                  color: chartType === type ? 'var(--accent, #58a6ff)' : 'var(--text-primary, #c9d1d9)',
                  borderRadius: 6,
                  padding: '8px 10px',
                  fontSize: 12,
                  fontWeight: 500,
                  textTransform: 'capitalize',
                  cursor: 'pointer',
                }}
              >
                {type === 'line' ? '📈 Line Chart' : type === 'bar' ? '📊 Bar Chart' : '🔢 Scalar Card'}
              </button>
            ))}
          </div>
        </div>

        {/* Custom Title */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-secondary, #8b949e)' }}>
            Panel Title (Optional)
          </label>
          <input
            type="text"
            placeholder={activeKey || 'My Chart Title'}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            style={{
              background: 'var(--bg-secondary, #161b22)',
              color: 'var(--text-primary, #c9d1d9)',
              border: '1px solid var(--border-dim, #30363d)',
              borderRadius: 4,
              padding: '0 8px',
              fontSize: 13,
              outline: 'none',
            }}
          />
        </div>

        {/* Y-Scale if Line */}
        {chartType === 'line' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-secondary, #8b949e)' }}>
              Y-Axis Scale:
            </label>
            <label style={{ fontSize: 12, color: 'var(--text-primary, #c9d1d9)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <input
                type="radio"
                name="scale"
                checked={scale === 'linear'}
                onChange={() => setScale('linear')}
              />
              Linear
            </label>
            <label style={{ fontSize: 12, color: 'var(--text-primary, #c9d1d9)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <input
                type="radio"
                name="scale"
                checked={scale === 'log'}
                onChange={() => setScale('log')}
              />
              Logarithmic
            </label>
          </div>
        )}

        {/* Actions */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 8 }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'none',
              border: '1px solid var(--border-dim, #30363d)',
              color: 'var(--text-secondary, #8b949e)',
              borderRadius: 4,
              padding: '6px 12px',
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            type="submit"
            style={{
              background: 'var(--accent, #1f6feb)',
              border: 'none',
              color: '#fff',
              borderRadius: 4,
              padding: '6px 16px',
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Add Panel
          </button>
        </div>
      </form>
    </div>
  );
}
