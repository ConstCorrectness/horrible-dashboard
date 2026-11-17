/**
 * A tab group's strip: the tabs, then the group's verbs.
 *
 * Only the chrome. The pane bodies are not children of this — they live in the
 * workbench's flat pane layer, positioned over the group's body, so a tab can
 * change groups without its pane being torn down (see Workbench.tsx).
 */
import { memo } from 'react';
import {
  canPopOut,
  closePaneGuarded,
  dockPopoutBack,
  layoutStore,
  openContextMenu,
  paneDisplayTitle,
  popOutPane,
  presentPane,
  resolveView,
  toggleDock,
  toggleMaximizedGroup,
  type AreaNode,
  type AreaHost,
} from '@horrible/core';

import { useHorizontalWheel } from '../hooks/useHorizontalWheel';
import {
  CloseIcon,
  DockBackIcon,
  FullscreenIcon,
  LeaveFullscreenIcon,
  MaximizeIcon,
  MinimizeIcon,
  PopOutIcon,
  RestoreIcon,
} from '../layout/WindowChrome';
import { TAB_H } from './layout';

export interface TabPointerDown {
  (e: React.PointerEvent, instanceId: string, areaId: string): void;
}

export const GroupHeader = memo(function GroupHeader({
  area,
  host,
  focused,
  maximized,
  presentedInstanceId,
  onTabPointerDown,
  suppressClick,
}: {
  area: AreaNode;
  host: AreaHost;
  focused: boolean;
  maximized: boolean;
  presentedInstanceId: string | null;
  onTabPointerDown: TabPointerDown;
  /** True right after a tab drag, so the release does not also count as a click. */
  suppressClick: () => boolean;
}) {
  const wheelRef = useHorizontalWheel<HTMLDivElement>();
  const active = area.tabs[area.activeTab];
  const decl = active ? resolveView(active.viewId) : undefined;
  const presented = !!active && presentedInstanceId === active.instanceId;
  const dockSide = host.kind === 'dock' ? host.side : null;
  const popoutId = host.kind === 'popout' ? host.popoutId : null;

  return (
    <div
      className="wb-strip"
      data-wb-strip={area.id}
      style={{
        height: TAB_H,
        display: 'flex',
        alignItems: 'stretch',
        boxSizing: 'border-box',
        borderTop: `2px solid ${focused ? 'var(--accent)' : 'transparent'}`,
        borderBottom: '1px solid var(--border)',
        background: focused
          ? 'var(--win-titlebar-bg-active, var(--bg-elevated))'
          : 'var(--win-titlebar-bg, var(--bg-raised))',
      }}
      onDoubleClick={(e) => {
        // On the empty strip only: a double-click on a tab is two clicks on it.
        if ((e.target as HTMLElement).closest('[data-wb-tab]')) return;
        if (popoutId) return;
        toggleMaximizedGroup(area.id);
      }}
      onContextMenu={(e) => {
        const tab = (e.target as HTMLElement).closest<HTMLElement>('[data-wb-tab]');
        const instanceId = tab?.dataset.wbTab ?? active?.instanceId;
        if (!instanceId) return;
        // The kind keeps its old name: the help items and any plugin that adds to
        // this menu are keyed on it.
        if (openContextMenu(e, { kind: 'window.titlebar', areaId: area.id, instanceId })) {
          e.preventDefault();
          e.stopPropagation();
        }
      }}
    >
      <div
        ref={wheelRef}
        role="tablist"
        aria-label={dockSide ? `${dockSide} dock` : 'Tabs'}
        style={{ display: 'flex', minWidth: 0, flex: 1, overflow: 'hidden' }}
      >
        {area.tabs.map((tab, i) => {
          const selected = i === area.activeTab;
          return (
            <button
              key={tab.instanceId}
              type="button"
              role="tab"
              className={`wb-tab${selected ? ' is-active' : ''}`}
              data-wb-tab={tab.instanceId}
              data-wb-tab-index={i}
              aria-selected={selected}
              title={paneDisplayTitle(tab)}
              onPointerDown={(e) => onTabPointerDown(e, tab.instanceId, area.id)}
              onClick={() => {
                if (suppressClick()) return;
                layoutStore.dispatch({ type: 'SET_ACTIVE_TAB', areaId: area.id, index: i });
              }}
              onAuxClick={(e) => {
                if (e.button === 1) {
                  e.preventDefault();
                  void closePaneGuarded(tab.instanceId);
                }
              }}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                maxWidth: 200,
                minWidth: 0,
                height: '100%',
                padding: '0 0.75rem',
                border: 0,
                borderRight: '1px solid var(--border)',
                background: selected ? 'var(--bg-raised)' : 'transparent',
                color: selected ? 'var(--text-strong)' : 'var(--text-secondary, var(--text-muted))',
                font: 'inherit',
                fontSize: 11,
                fontWeight: 650,
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
                cursor: 'default',
                whiteSpace: 'nowrap',
              }}
            >
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {paneDisplayTitle(tab)}
              </span>
              {tab.attention ? (
                <span
                  aria-label="finished"
                  style={{
                    width: 6,
                    height: 6,
                    flex: 'none',
                    background: 'var(--accent)',
                    clipPath: 'polygon(50% 0, 100% 50%, 50% 100%, 0 50%)',
                  }}
                />
              ) : null}
            </button>
          );
        })}
      </div>
      <div style={{ display: 'flex', alignItems: 'stretch', flex: 'none' }}>
        {decl?.fullscreen && active && (
          <StripButton
            label={presented ? 'Leave fullscreen (Esc)' : 'Fullscreen'}
            pressed={presented}
            onClick={() => presentPane(presented ? null : active.instanceId)}
          >
            {presented ? <LeaveFullscreenIcon /> : <FullscreenIcon />}
          </StripButton>
        )}
        {!popoutId && active && canPopOut(active.viewId) && (
          <StripButton label="Pop out into its own window" onClick={() => popOutPane(active.instanceId)}>
            <PopOutIcon />
          </StripButton>
        )}
        {popoutId && (
          <StripButton label="Dock back into the workbench" onClick={() => dockPopoutBack(popoutId)}>
            <DockBackIcon />
          </StripButton>
        )}
        {dockSide && (
          <StripButton
            label={`Hide the ${dockSide} dock`}
            onClick={() => toggleDock(dockSide, false)}
          >
            <MinimizeIcon />
          </StripButton>
        )}
        {/* A pop-out is its own window: the OS maximizes it. */}
        {!popoutId && (
          <StripButton
            label={maximized ? 'Restore' : 'Maximize'}
            pressed={maximized}
            onClick={() => toggleMaximizedGroup(area.id)}
          >
            {maximized ? <RestoreIcon /> : <MaximizeIcon />}
          </StripButton>
        )}
        {active && (
          <StripButton
            label="Close"
            danger
            onClick={() => void closePaneGuarded(active.instanceId)}
          >
            <CloseIcon />
          </StripButton>
        )}
      </div>
    </div>
  );
});

function StripButton({
  label,
  pressed,
  danger,
  onClick,
  children,
}: {
  label: string;
  pressed?: boolean;
  danger?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      className={`wb-strip-btn${danger ? ' is-danger' : ''}`}
      title={label}
      aria-label={label}
      aria-pressed={pressed}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={onClick}
      style={{
        width: 32,
        display: 'grid',
        placeItems: 'center',
        border: 0,
        padding: 0,
        background: 'transparent',
        color: 'var(--text-secondary, var(--text-muted))',
      }}
    >
      {children}
    </button>
  );
}
