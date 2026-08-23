import { useEffect, useState } from 'react';

import { Button } from '../../Primitives';
import { RunsSidebar } from './components/RunsSidebar';
import { RunDetailsModal } from './components/RunDetailsModal';
import { WorkspaceGrid } from './components/WorkspaceGrid';
import { localTrackStore, useLocalTrackStore } from './store';

/**
 * The LocalTrack pane.
 *
 * Two things this root used to do that it no longer does.
 *
 * It painted `background: var(--bg-primary, #0d1117)` and
 * `fontFamily: 'system-ui, …'` — a fixed GitHub-dark ground and a hardcoded font
 * stack. `--bg-primary` was declared nowhere, so the fallback always won and the
 * pane rendered the same dark rectangle in all six themes, ignoring the switcher
 * entirely. The token is defined now (as an alias of `--bg`), and the font comes
 * from `--font-sans`, which is per-theme — studio's DM Sans was never reaching
 * this pane.
 *
 * And it rendered neither `error` nor `loading`, both of which the store has
 * always tracked. `error` is set in six separate places; every one of them was
 * invisible, so a failed fetch looked exactly like an empty project.
 */
export function LocalTrackWorkspacePane() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const { error, loading, dismissError } = useLocalTrackStore();

  useEffect(() => {
    void localTrackStore.init();
  }, []);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        width: '100%',
        height: '100%',
        overflow: 'hidden',
        background: 'var(--bg)',
        color: 'var(--text)',
        fontFamily: 'var(--font-sans)',
      }}
    >
      {/* Dismissible rather than transient: a failed save is still true a minute
          later, and a toast that has already faded cannot be re-read. */}
      {error && (
        <div
          role="alert"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-4)',
            padding: 'var(--space-3) var(--space-5)',
            borderBottom: '1px solid var(--danger)',
            background: 'color-mix(in srgb, var(--danger) 12%, transparent)',
            color: 'var(--danger)',
            fontSize: 'var(--fs-body)',
          }}
        >
          <span style={{ flex: 1 }}>{error}</span>
          <Button intent="ghost" size="sm" onClick={dismissError}>
            Dismiss
          </Button>
        </div>
      )}

      {/* A 2px bar rather than a spinner over the content: the charts under it
          are still readable and still the previous truth, which is what you want
          while a refresh is in flight. */}
      {loading && (
        <div
          aria-hidden
          style={{
            height: 2,
            flex: 'none',
            background:
              'linear-gradient(90deg, transparent, var(--accent), transparent) 0 0 / 40% 100% no-repeat',
            animation: 'hd-lt-scan 1.1s linear infinite',
          }}
        />
      )}

      <style>
        {'@keyframes hd-lt-scan{from{background-position:-40% 0}to{background-position:140% 0}}'}
      </style>

      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <RunsSidebar
          collapsed={sidebarCollapsed}
          onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
        />
        <WorkspaceGrid />
      </div>

      <RunDetailsModal />
    </div>
  );
}
