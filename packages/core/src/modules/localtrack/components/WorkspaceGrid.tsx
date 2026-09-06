/**
 * The chart workspace: the filtered grid of metric panels.
 *
 * Converted to the shared primitives and the scale tokens. The three emoji that
 * were doing icon duty here (🔍 in the filter, ✕ to clear it, 📊 in the empty
 * state) are gone — the first two are vector strokes, and the third was pure
 * decoration on a message that is better off saying what to do.
 *
 * The empty state also stopped conflating two different situations. "You have no
 * panels" and "your filter matches none of them" want opposite advice, and the
 * single message covering both ("No panels match your filter or none are
 * configured") gave neither.
 */
import { useMemo, useState } from 'react';

import { Button, EmptyState } from '../../../Primitives';
import { useLocalTrackStore } from '../store';
import { AddPanelModal } from './AddPanelModal';
import { BarPanel } from './BarPanel';
import { ChartPanel } from './ChartPanel';
import { ScalarPanel } from './ScalarPanel';

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

function IconSearch() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" {...stroke} aria-hidden>
      <circle cx="5" cy="5" r="3.2" />
      <path d="M7.4 7.4 10.5 10.5" />
    </svg>
  );
}

function IconClear() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" {...stroke} aria-hidden>
      <path d="M3 3l6 6M9 3l-6 6" />
    </svg>
  );
}

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

  const visiblePanels = useMemo(() => {
    if (!searchRegex.trim()) return panels;
    try {
      const re = new RegExp(searchRegex.trim(), 'i');
      return panels.filter((p) => re.test(p.title) || re.test(p.metricKey));
    } catch {
      // A half-typed regex ("eval/[") is a normal intermediate state, not an
      // error — falling back to substring keeps the list responsive per keystroke
      // instead of emptying it until the brackets balance.
      const q = searchRegex.toLowerCase();
      return panels.filter(
        (p) => p.title.toLowerCase().includes(q) || p.metricKey.toLowerCase().includes(q),
      );
    }
  }, [panels, searchRegex]);

  const filtering = searchRegex.trim().length > 0;

  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
        background: 'var(--bg)',
      }}
    >
      <div
        style={{
          padding: 'var(--space-4) var(--space-6)',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--space-6)',
          background: 'var(--bg-raised)',
          flexWrap: 'wrap',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-3)',
            flex: 1,
            maxWidth: 360,
            color: 'var(--text-dim)',
          }}
        >
          <IconSearch />
          <input
            type="text"
            placeholder="Filter panels — regex or plain text"
            value={searchRegex}
            onChange={(e) => setSearchRegex(e.target.value)}
            style={{
              width: '100%',
              background: 'var(--bg-inset)',
              color: 'var(--text)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              // Horizontal padding only: `controls.css` fixes this control's height and
              // strips its vertical padding (the One Height Rule) — see theming.mdx.
              padding: '0 var(--space-4)',
              fontSize: 'var(--fs-meta)',
            }}
          />
          {filtering && (
            <Button
              intent="ghost"
              size="sm"
              onClick={() => setSearchRegex('')}
              title="Clear filter"
              icon={<IconClear />}
            />
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-6)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            <span
              style={{
                fontSize: 'var(--fs-meta)',
                fontWeight: 'var(--fw-bold)',
                letterSpacing: 'var(--tracking-badge)',
                textTransform: 'uppercase',
                color: 'var(--text-dim)',
              }}
            >
              Smoothing
            </span>
            <input
              type="range"
              min="0"
              max="0.99"
              step="0.01"
              value={globalSmoothing}
              onChange={(e) => setGlobalSmoothing(parseFloat(e.target.value))}
              aria-label="EMA smoothing"
              style={{ width: 110, cursor: 'pointer', accentColor: 'var(--accent)' }}
            />
            <span
              style={{
                fontSize: 'var(--fs-meta)',
                fontFamily: 'var(--font-mono)',
                color: 'var(--text)',
                minWidth: 32,
                // Without tabular figures the readout jiggles as you drag,
                // because the digits are different widths.
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {globalSmoothing.toFixed(2)}
            </span>
          </div>

          <div style={{ height: 18, width: 1, background: 'var(--border)' }} />

          <Button size="sm" onClick={resetPanels} title="Restore the default panels">
            Reset
          </Button>
          <Button intent="primary" size="sm" onClick={() => setShowAddModal(true)}>
            Add panel
          </Button>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-6)' }}>
        {visiblePanels.length === 0 ? (
          filtering ? (
            <EmptyState
              title="No panel matches"
              actions={
                <Button size="sm" onClick={() => setSearchRegex('')}>
                  Clear filter
                </Button>
              }
            >
              Nothing in this workspace matches <code>{searchRegex}</code>. The filter reads as a
              regular expression, falling back to plain text while it is incomplete.
            </EmptyState>
          ) : (
            <EmptyState
              title="No panels"
              actions={
                <>
                  <Button intent="primary" size="sm" onClick={() => setShowAddModal(true)}>
                    Add panel
                  </Button>
                  <Button size="sm" onClick={resetPanels}>
                    Restore defaults
                  </Button>
                </>
              }
            >
              Add a chart for any metric your runs have logged, or restore the four defaults
              (training loss, eval loss, eval accuracy, learning rate).
            </EmptyState>
          )
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))',
              gap: 'var(--space-6)',
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
                  <BarPanel key={panel.id} panel={panel} onRemove={() => removePanel(panel.id)} />
                );
              }
              return (
                <ChartPanel key={panel.id} panel={panel} onRemove={() => removePanel(panel.id)} />
              );
            })}
          </div>
        )}
      </div>

      <AddPanelModal isOpen={showAddModal} onClose={() => setShowAddModal(false)} />
    </div>
  );
}
