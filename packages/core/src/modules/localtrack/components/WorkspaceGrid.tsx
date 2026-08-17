import { useMemo, useState } from 'react';
import { useLocalTrackStore } from '../store';
import { AddPanelModal } from './AddPanelModal';
import { BarPanel } from './BarPanel';
import { ChartPanel } from './ChartPanel';
import { ScalarPanel } from './ScalarPanel';

export function WorkspaceGrid() {
  const {
    panels,
    removePanel,
    resetPanels,
    searchRegex,
    setSearchRegex,
    globalSmoothing,
    setGlobalSmoothing,
  } = useLocalTrackStore();

  const [showAddModal, setShowAddModal] = useState(false);

  // Regex-filtered panels
  const visiblePanels = useMemo(() => {
    if (!searchRegex.trim()) return panels;
    try {
      const re = new RegExp(searchRegex.trim(), 'i');
      return panels.filter((p) => re.test(p.title) || re.test(p.metricKey));
    } catch {
      // Fallback to substring match if regex is incomplete
      const q = searchRegex.toLowerCase();
      return panels.filter(
        (p) => p.title.toLowerCase().includes(q) || p.metricKey.toLowerCase().includes(q)
      );
    }
  }, [panels, searchRegex]);

  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
        background: 'var(--bg-primary, #0d1117)',
      }}
    >
      {/* Top Workspace Toolbar */}
      <div
        style={{
          padding: '10px 18px',
          borderBottom: '1px solid var(--border-dim, #30363d)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 16,
          background: 'var(--bg-secondary, #161b22)',
          flexWrap: 'wrap',
        }}
      >
        {/* Left: Regex Filter Search Bar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, maxWidth: 360 }}>
          <span style={{ fontSize: 14, color: 'var(--text-dim, #8b949e)' }}>🔍</span>
          <input
            type="text"
            placeholder="Regex filter panels (e.g. .*loss.*, acc)..."
            value={searchRegex}
            onChange={(e) => setSearchRegex(e.target.value)}
            style={{
              width: '100%',
              background: 'var(--bg-tertiary, #0d1117)',
              color: 'var(--text-primary, #c9d1d9)',
              border: '1px solid var(--border-dim, #30363d)',
              borderRadius: 6,
              padding: '6px 10px',
              fontSize: 12,
              outline: 'none',
            }}
          />
          {searchRegex && (
            <button
              onClick={() => setSearchRegex('')}
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

        {/* Right: Smoothing Slider & Add Panel */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          {/* EMA Smoothing Slider */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary, #8b949e)', fontWeight: 500 }}>
              EMA Smoothing:
            </span>
            <input
              type="range"
              min="0"
              max="0.99"
              step="0.01"
              value={globalSmoothing}
              onChange={(e) => setGlobalSmoothing(parseFloat(e.target.value))}
              style={{
                width: 110,
                cursor: 'pointer',
                accentColor: 'var(--accent, #58a6ff)',
              }}
            />
            <span
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: 'var(--text-primary, #c9d1d9)',
                minWidth: 32,
                fontFamily: 'monospace',
              }}
            >
              {globalSmoothing.toFixed(2)}
            </span>
          </div>

          <div style={{ height: 18, width: 1, background: 'var(--border-dim, #30363d)' }} />

          {/* Reset Layout */}
          <button
            onClick={resetPanels}
            title="Reset default panels"
            style={{
              background: 'none',
              border: '1px solid var(--border-dim, #30363d)',
              color: 'var(--text-secondary, #8b949e)',
              borderRadius: 6,
              padding: '6px 10px',
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            Reset
          </button>

          {/* + Add Panel Button */}
          <button
            onClick={() => setShowAddModal(true)}
            style={{
              background: 'var(--accent, #1f6feb)',
              border: 'none',
              color: '#fff',
              borderRadius: 6,
              padding: '6px 14px',
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              boxShadow: '0 2px 6px rgba(31, 111, 235, 0.4)',
            }}
          >
            <span>+</span> Add Panel
          </button>
        </div>
      </div>

      {/* Grid Content Area */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: 16,
        }}
      >
        {visiblePanels.length === 0 ? (
          <div
            style={{
              height: '100%',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--text-dim, #8b949e)',
              gap: 12,
            }}
          >
            <span style={{ fontSize: 24 }}>📊</span>
            <span>No panels match your filter or none are configured.</span>
            <button
              onClick={() => setShowAddModal(true)}
              style={{
                background: 'var(--accent, #1f6feb)',
                color: '#fff',
                border: 'none',
                borderRadius: 4,
                padding: '6px 12px',
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              + Add Panel
            </button>
          </div>
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))',
              gap: 16,
              alignItems: 'start',
            }}
          >
            {visiblePanels.map((panel) => {
              if (panel.chartType === 'scalar') {
                return (
                  <ScalarPanel
                    key={panel.id}
                    panel={panel}
                    onRemove={() => removePanel(panel.id)}
                  />
                );
              }
              if (panel.chartType === 'bar') {
                return (
                  <BarPanel
                    key={panel.id}
                    panel={panel}
                    onRemove={() => removePanel(panel.id)}
                  />
                );
              }
              return (
                <ChartPanel
                  key={panel.id}
                  panel={panel}
                  onRemove={() => removePanel(panel.id)}
                />
              );
            })}
          </div>
        )}
      </div>

      <AddPanelModal isOpen={showAddModal} onClose={() => setShowAddModal(false)} />
    </div>
  );
}
