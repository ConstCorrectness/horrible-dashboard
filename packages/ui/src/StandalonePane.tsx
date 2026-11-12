/**
 * One pane, full-window, with no desktop around it: what `horrible run <id>` shows.
 *
 * The pane renders through the same `PaneHost` the desktop uses, so it gets the same
 * contexts (instance id, params, focus, capture) and behaves as it does in a window.
 * What the desktop's window chrome would add, the region strips, is laid out here
 * as fixed columns and rows: a module that put its queue in a left strip gets a left
 * column. Strips are not collapsible here; a standalone window *is* the module.
 */
import { useEffect, useState, type CSSProperties } from 'react';
import { resolveView, type PaneState } from '@horrible/core';

import { PaneHost } from './layout/PaneHost';
import { Toasts } from './Toasts';
import './styles.css';
import './layout/frame.css';

type Region = { id: string; label: string; position: string; size: number };

function regionsOf(viewId: string): Region[] {
  const decl = resolveView(viewId) as { regions?: Array<Record<string, unknown>> } | undefined;
  return (decl?.regions ?? []).map((r) => ({
    id: String(r.id),
    label: String(r.label ?? r.id),
    position: String(r.position ?? 'right'),
    size: Number(r.defaultSize ?? 300),
  }));
}

function pane(viewId: string): PaneState {
  return { instanceId: `${viewId}#standalone`, viewId };
}

const styles = {
  root: {
    position: 'fixed',
    inset: 0,
    display: 'grid',
    background: 'var(--bg)',
    color: 'var(--text)',
  },
  cell: {
    position: 'relative',
    minWidth: 0,
    minHeight: 0,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  strip: {
    borderColor: 'var(--border)',
    borderStyle: 'solid',
    borderWidth: 0,
    background: 'var(--surface)',
  },
  stripTitle: {
    flex: '0 0 auto',
    padding: '0.4rem 0.7rem',
    fontSize: '0.68rem',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.14em',
    color: 'var(--text-secondary)',
    borderBottom: '1px solid var(--border)',
  },
  body: { flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column' },
} satisfies Record<string, CSSProperties>;

function Strip({ region, edge }: { region: Region; edge: keyof CSSProperties }) {
  return (
    <section style={{ ...styles.cell, ...styles.strip, [edge]: 1 }} aria-label={region.label}>
      <div style={styles.stripTitle}>{region.label}</div>
      <div style={styles.body}>
        <PaneHost pane={pane(region.id)} />
      </div>
    </section>
  );
}

/** The pane itself never gets less than this beside its strips; below it, they stack. */
const MIN_PANE_WIDTH = 480;

function useWindowWidth(): number {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const on = () => setWidth(window.innerWidth);
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  return width;
}

export function StandalonePane({ viewId }: { viewId: string }) {
  const regions = regionsOf(viewId);
  const width = useWindowWidth();
  const at = (p: string) => regions.filter((r) => r.position === p);
  const sideWidth = [...at('left'), ...at('right')].reduce((n, r) => n + r.size, 0);
  // Too narrow for side columns: everything stacks, the pane first. Strips sized
  // for a 1400px desktop window would otherwise squeeze the pane to nothing.
  if (sideWidth > 0 && width < sideWidth + MIN_PANE_WIDTH) {
    return (
      <div
        style={{
          ...styles.root,
          gridTemplateColumns: 'minmax(0, 1fr)',
          gridTemplateRows: `minmax(60vh, 1fr) ${regions.map(() => 'minmax(40vh, auto)').join(' ')}`,
          overflowY: 'auto',
        }}
      >
        <main style={styles.cell}>
          <PaneHost pane={pane(viewId)} />
        </main>
        {regions.map((r) => (
          <Strip key={r.id} region={r} edge="borderTopWidth" />
        ))}
        <Toasts />
      </div>
    );
  }
  const left = at('left');
  const right = at('right');
  const bottom = at('bottom');
  const cols = [
    ...left.map((r) => `${r.size}px`),
    'minmax(0, 1fr)',
    ...right.map((r) => `${r.size}px`),
  ].join(' ');
  const rows = ['minmax(0, 1fr)', ...bottom.map((r) => `${r.size}px`)].join(' ');
  const span = `1 / ${left.length + right.length + 2}`;

  return (
    <div style={{ ...styles.root, gridTemplateColumns: cols, gridTemplateRows: rows }}>
      {left.map((r) => (
        <Strip key={r.id} region={r} edge="borderRightWidth" />
      ))}
      <main style={styles.cell}>
        <PaneHost pane={pane(viewId)} />
      </main>
      {right.map((r) => (
        <Strip key={r.id} region={r} edge="borderLeftWidth" />
      ))}
      {bottom.map((r) => (
        <div key={r.id} style={{ gridColumn: span, display: 'flex', minHeight: 0 }}>
          <Strip region={r} edge="borderTopWidth" />
        </div>
      ))}
      <Toasts />
    </div>
  );
}
