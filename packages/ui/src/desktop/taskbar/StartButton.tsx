/**
 * The start button and its menu: the launcher for everything the app can open.
 *
 * The list is the registry's, filtered exactly the way the command palette's
 * openers are — `embedded` views are excluded, because they live inside a host
 * pane and a launcher entry would present one as a second, competing home for
 * content that already has one.
 *
 * Two things beyond a flat list:
 *
 * - **It is grouped by the module a pane belongs to.** Sixty-odd entries in one
 *   alphabetical run told the user nothing, but the previous grouping — by
 *   `PaneRole`, as Documents / Tools / Widgets — answered a question nobody
 *   browsing a launcher is asking: the role decides where a pane *lands* by
 *   default, and everything here opens and tiles either way. The feature a pane
 *   belongs to is what someone is actually looking for, and it is the axis the
 *   search box already matched on. Searching flattens the groups, because a
 *   filtered list of four things does not need headings.
 * - **It has a settings footer.** The bottom-left corner is where people go for
 *   settings, and this menu previously offered it only as one row among sixty,
 *   sorted under S.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import {
  layoutStore,
  openContextMenu,
  registry,
  resolveViewIcon,
  useWorkspaces,
  type PanelDecl,
  type WidgetDecl,
} from '@horrible/core';

export function StartButton({ showLabels }: { showLabels: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="os-start">
      <button
        type="button"
        className={`os-start-btn${open ? ' is-open' : ''}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Start"
        onClick={() => setOpen((v) => !v)}
      >
        <img src="/logo.svg" alt="" aria-hidden="true" />
        {showLabels && <span>Start</span>}
      </button>
      {open && <StartMenu onClose={() => setOpen(false)} />}
    </div>
  );
}

type View = PanelDecl | WidgetDecl;

/** Where a pane with no owning module is filed. Last, and named for what it is. */
const UNGROUPED = 'Other';

/**
 * The views, bucketed by owning module and sorted for browsing.
 *
 * Headings are alphabetical and `Other` is pinned last — a module that declares
 * no owner is a plugin gap, not a category anyone chose, so it should not sort
 * into the middle of real feature names.
 */
function groupByOwner(views: View[]): { label: string; views: View[] }[] {
  const buckets = new Map<string, View[]>();
  for (const v of views) {
    const owner = registry.viewOwner(v.id) ?? UNGROUPED;
    const bucket = buckets.get(owner);
    if (bucket) bucket.push(v);
    else buckets.set(owner, [v]);
  }
  return [...buckets.entries()]
    .map(([label, group]) => ({
      label,
      views: [...group].sort((a, b) => a.title.localeCompare(b.title)),
    }))
    .sort((a, b) => {
      if (a.label === UNGROUPED) return 1;
      if (b.label === UNGROUPED) return -1;
      return a.label.localeCompare(b.label);
    });
}

function StartMenu({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const views = useMemo<View[]>(
    () =>
      [...registry.panels, ...registry.widgets]
        .filter((v) => !v.embedded)
        .sort((a, b) => a.title.localeCompare(b.title)),
    [],
  );
  const searching = query.trim().length > 0;
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return views;
    // Matched against the owning **module** as well as the pane's own title,
    // because the two are often different words for one feature: the
    // Observability module's pane is called "Data flow", so a search for the
    // thing the user came looking for used to come back empty on a pane that
    // was right there. The id is matched too, but an id is not something anyone
    // types on purpose.
    return views.filter(
      (v) =>
        v.title.toLowerCase().includes(q) ||
        v.id.includes(q) ||
        (registry.viewOwner(v.id)?.toLowerCase().includes(q) ?? false),
    );
  }, [views, query]);
  const groups = useMemo(() => (searching ? [] : groupByOwner(matches)), [matches, searching]);

  useEffect(() => {
    inputRef.current?.focus();
    // Pointerdown, not click: a click listener fires after the button's own
    // onClick has already toggled `open` back on, so the menu reopens instead of
    // closing when you click the start button a second time.
    const onDown = (e: PointerEvent) => {
      if (
        !ref.current?.contains(e.target as Node) &&
        !(e.target as HTMLElement).closest('.os-start-btn')
      ) {
        onClose();
      }
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [onClose]);

  const launch = (id: string) => {
    registry.openPanel(id);
    onClose();
  };

  return (
    <div className="os-start-menu" ref={ref} role="menu" aria-label="Start menu">
      <input
        ref={inputRef}
        className="os-start-search"
        type="search"
        placeholder="Search…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') onClose();
          // Enter launches the single remaining match, so a search that narrows
          // to one thing does not then require a mouse.
          if (e.key === 'Enter' && matches.length) launch(matches[0].id);
        }}
      />
      <div className="os-start-list">
        {/* A search that has narrowed to a handful does not need headings, and
            grouping four results under three labels reads as more structure
            than there is content. Groups are for browsing. */}
        {searching
          ? matches.map((v) => <StartItem key={v.id} view={v} onLaunch={launch} />)
          : groups.map(({ label, views: group }) => (
              <div key={label} className="os-start-group">
                <h3 className="os-start-group-head">{label}</h3>
                {group.map((v) => (
                  <StartItem key={v.id} view={v} onLaunch={launch} />
                ))}
              </div>
            ))}
        {!matches.length && <p className="os-start-empty">Nothing matches “{query}”.</p>}
        {!searching && <DesktopsGroup onClose={onClose} />}
      </div>
      {/* The footer. Settings is what people come to this corner for, and it was
          previously reachable only as one row of sixty, filed under S. */}
      <div className="os-start-footer">
        <button
          type="button"
          role="menuitem"
          className="os-start-foot-btn"
          onClick={() => launch('settings.home')}
        >
          <span aria-hidden="true">⚙</span> Settings
        </button>
        <button
          type="button"
          role="menuitem"
          className="os-start-foot-btn"
          onClick={() => {
            void registry.runCommand('shell.setup');
            onClose();
          }}
        >
          <span aria-hidden="true">✦</span> Setup
        </button>
        <button
          type="button"
          role="menuitem"
          className="os-start-foot-btn"
          aria-haspopup="menu"
          // The tray's picker, reused by kind rather than rebuilt — one list of
          // themes, wherever it is opened from.
          onClick={(ev) => {
            const r = ev.currentTarget.getBoundingClientRect();
            openContextMenu({ clientX: r.left, clientY: r.top }, { kind: 'taskbar.theme' });
            onClose();
          }}
        >
          <span aria-hidden="true">◐</span> Theme
        </button>
      </div>
    </div>
  );
}

/**
 * Desktops: switch between them, and manage them.
 *
 * This is the **home for workspace management** now that the top strip hides itself
 * on a floating desktop. The strip was the only surface that could rename, create,
 * reset and delete a workspace, so hiding it without moving management would have
 * left those verbs unreachable — the constraint that kept the taskbar's pips off by
 * default in the first place.
 *
 * The pips in the taskbar switch; this switches *and* manages, which is the split
 * that made two always-visible switchers feel redundant before. Only one of them is
 * always visible now.
 */
function DesktopsGroup({ onClose }: { onClose: () => void }) {
  const { workspaces, activeId } = useWorkspaces();
  const { frame } = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const presetIds = new Set(registry.framePresets.map((p) => p.id));
  const run = (command: string) => {
    void registry.runCommand(command);
    onClose();
  };
  return (
    <div className="os-start-group">
      <h3 className="os-start-group-head">Desktops</h3>
      {workspaces.map((w) => (
        <button
          key={w.id}
          type="button"
          role="menuitem"
          className={`os-start-item${w.id === activeId ? ' is-active' : ''}`}
          aria-current={w.id === activeId}
          onClick={() => {
            registry.switchWorkspace(w.id);
            onClose();
          }}
        >
          <span className="os-start-icon" aria-hidden="true">
            {w.id === activeId ? (frame.mode === 'tiling' ? '▦' : '❐') : '·'}
          </span>
          <span className="os-start-title">{w.name}</span>
        </button>
      ))}
      <button
        type="button"
        role="menuitem"
        className="os-start-item"
        onClick={() => run('workspace.new')}
      >
        <span className="os-start-icon" aria-hidden="true">
          ▦
        </span>
        <span className="os-start-title">New tiled desktop</span>
      </button>
      <button
        type="button"
        role="menuitem"
        className="os-start-item"
        onClick={() => run('workspace.newFloating')}
      >
        <span className="os-start-icon" aria-hidden="true">
          ❐
        </span>
        <span className="os-start-title">New floating desktop</span>
      </button>
      <button
        type="button"
        role="menuitem"
        className="os-start-item"
        onClick={() => run('workspace.saveAs')}
      >
        <span className="os-start-icon" aria-hidden="true">
          ⎘
        </span>
        <span className="os-start-title">Save this arrangement as a desktop</span>
      </button>
      <button
        type="button"
        role="menuitem"
        className="os-start-item"
        onClick={() => run('workspace.rename')}
      >
        <span className="os-start-icon" aria-hidden="true">
          ✎
        </span>
        <span className="os-start-title">Rename this desktop</span>
      </button>
      {/* Reset for a preset, delete for a custom one — the same either/or the tab
          strip's menu makes, and for the same reason: a preset's tab comes straight
          back from its manifest, so "delete" would be a lie. */}
      {activeId && presetIds.has(activeId) ? (
        <button
          type="button"
          role="menuitem"
          className="os-start-item"
          onClick={() => run('layout.reset')}
        >
          <span className="os-start-icon" aria-hidden="true">
            ↺
          </span>
          <span className="os-start-title">Reset this desktop to its preset</span>
        </button>
      ) : (
        <button
          type="button"
          role="menuitem"
          className="os-start-item is-danger"
          onClick={() => run('workspace.delete')}
        >
          <span className="os-start-icon" aria-hidden="true">
            ✕
          </span>
          <span className="os-start-title">Delete this desktop</span>
        </button>
      )}
    </div>
  );
}

function StartItem({ view, onLaunch }: { view: View; onLaunch: (id: string) => void }) {
  // No module suffix any more: the owning module is the heading this row sits
  // under, and repeating it on every row is the "Settings · Settings" noise the
  // suffix was already dodging half the time.
  const icon = resolveViewIcon(view.id, view.icon, view.title);
  return (
    <button
      type="button"
      role="menuitem"
      className="os-start-item"
      onClick={() => onLaunch(view.id)}
    >
      <span className="os-start-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="os-start-title">{view.title}</span>
    </button>
  );
}
