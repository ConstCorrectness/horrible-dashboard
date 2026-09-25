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
 * - **It is grouped into a handful of fixed categories** (`PaneCategory`), and a
 *   module with several panes folds into one row inside its category. Grouping by
 *   `PaneRole` answered a question nobody browsing asks; grouping by module gave
 *   fifty headings, most over a single row. Searching flattens the groups,
 *   because a filtered list of four things does not need headings.
 * - **It has a settings footer.** The bottom-left corner is where people go for
 *   settings, and this menu previously offered it only as one row among eighty,
 *   sorted under S.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import {
  layoutStore,
  openContextMenu,
  recentViewIds,
  PANE_CATEGORY_LABELS,
  registry,
  resolveViewIcon,
  subscribeRecents,
  useSetting,
  useWorkspaces,
  WORKSPACES_ENABLED_KEY,
  type PanelDecl,
  type WidgetDecl,
} from '@horrible/core';

import { DEFAULT_DESKTOP_MODE_KEY } from '../constants';

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

/** A module with several launchable panes, folded under its own row. */
interface ModuleFold {
  kind: 'fold';
  label: string;
  views: View[];
}
type CategoryEntry = { kind: 'view'; view: View } | ModuleFold;

/**
 * The views, bucketed into the fixed launcher categories (`PaneCategory`).
 *
 * Grouping by module gave one heading per feature — fifty-odd modules, most of
 * them owning a single pane — so the headings were either noise over one row or
 * gathered into a catch-all "All panes" band that was itself the longest list in
 * the menu. Seven categories answer the question someone browsing is asking
 * ("where are the research tools?"); inside one, a module with several panes
 * folds into a single row that opens in place, so a feature's panes stay together
 * without each costing a line of the category.
 */
function groupByCategory(views: View[]): { label: string; entries: CategoryEntry[] }[] {
  const byCategory = new Map<string, Map<string, View[]>>();
  for (const v of views) {
    const cat = registry.viewCategory(v.id);
    const owner = registry.viewOwner(v.id) ?? v.title;
    const modules = byCategory.get(cat) ?? new Map<string, View[]>();
    byCategory.set(cat, modules);
    const bucket = modules.get(owner);
    if (bucket) bucket.push(v);
    else modules.set(owner, [v]);
  }
  const byTitle = (a: View, b: View) => a.title.localeCompare(b.title);
  const out: { label: string; entries: CategoryEntry[] }[] = [];
  // Declaration order of the labels, not alphabetical: it runs from what this app
  // is for (research, agents) to its plumbing, with third-party plugins last.
  for (const [cat, label] of Object.entries(PANE_CATEGORY_LABELS)) {
    const modules = byCategory.get(cat);
    if (!modules) continue;
    const entries: CategoryEntry[] = [];
    for (const [owner, group] of modules) {
      if (group.length > 1)
        entries.push({ kind: 'fold', label: owner, views: group.sort(byTitle) });
      else entries.push({ kind: 'view', view: group[0] });
    }
    const key = (e: CategoryEntry) => (e.kind === 'fold' ? e.label : e.view.title);
    entries.sort((a, b) => key(a).localeCompare(key(b)));
    out.push({ label, entries });
  }
  return out;
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
    // Ranked, because Enter launches the first row: a search for "research" also
    // matches every pane in the AI Research category, and alphabetical order put
    // "Ablation sweep" ahead of the pane actually called Research.
    const rank = (v: View): number => {
      const title = v.title.toLowerCase();
      if (title === q) return 0;
      if (title.startsWith(q)) return 1;
      if (title.includes(q) || v.id.includes(q)) return 2;
      if (registry.viewOwner(v.id)?.toLowerCase().includes(q)) return 3;
      if (PANE_CATEGORY_LABELS[registry.viewCategory(v.id)].toLowerCase().includes(q)) return 4;
      return 5;
    };
    return views
      .map((v) => ({ v, r: rank(v) }))
      .filter(({ r }) => r < 5)
      .sort((a, b) => a.r - b.r)
      .map(({ v }) => v);
  }, [views, query]);
  const groups = useMemo(() => (searching ? [] : groupByCategory(matches)), [matches, searching]);

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
        {/* Recent first, and only while browsing: a search already knows what you
            are looking for, and a Recent band above the results would compete
            with them. Absent entirely until something has been opened, because
            an empty "Recent" heading is a promise the launcher has not kept. */}
        {!searching && <RecentGroup views={views} onLaunch={launch} />}
        {searching
          ? matches.map((v) => <StartItem key={v.id} view={v} onLaunch={launch} />)
          : groups.map(({ label, entries }) => (
              <div key={label} className="os-start-group">
                <h3 className="os-start-group-head">{label}</h3>
                {entries.map((e) =>
                  e.kind === 'view' ? (
                    <StartItem key={e.view.id} view={e.view} onLaunch={launch} />
                  ) : (
                    <StartFold key={e.label} fold={e} onLaunch={launch} />
                  ),
                )}
              </div>
            ))}
        {!matches.length && <p className="os-start-empty">Nothing matches “{query}”.</p>}
        {!searching && <DesktopsGroup onClose={onClose} />}
      </div>
      {/* The footer. Settings is what people come to this corner for, and it was
          previously reachable only as one row of sixty, filed under S. */}
      <div className="os-start-footer">
        {/* Home first: the taskbar is on screen in both paradigms, so this is the
            way back to the desktop from a tiled workspace, where the frame covers
            the home screen completely. */}
        <button
          type="button"
          role="menuitem"
          className="os-start-foot-btn"
          onClick={() => {
            void registry.runCommand('desktop.home');
            onClose();
          }}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <path
              d="M1.5 6 6 2l4.5 4M3 5v5h2.2V7.5h1.6V10H9V5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.2"
              strokeLinejoin="round"
            />
          </svg>{' '}
          Home
        </button>
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
          onClick={() => {
            void registry.runCommand('shell.help');
            onClose();
          }}
        >
          <span aria-hidden="true">?</span> Help
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
 * The panes you opened most recently.
 *
 * The launcher's answer to its own size. Fifty-one openers is a taxonomy, and a
 * taxonomy is the right structure for finding something the first time and the
 * wrong one for the fourth — most sessions reach for the same handful.
 *
 * Ids are resolved against the live `views` list rather than trusted: a pane can
 * be renamed, or come from a plugin that is no longer installed, and a Recent
 * entry that opens nothing is worse than no Recent entry.
 */
function RecentGroup({ views, onLaunch }: { views: View[]; onLaunch: (id: string) => void }) {
  const ids = useSyncExternalStore(subscribeRecents, recentViewIds, recentViewIds);
  const byId = useMemo(() => new Map(views.map((v) => [v.id, v])), [views]);
  const recent = ids.map((id) => byId.get(id)).filter((v): v is View => Boolean(v));
  if (!recent.length) return null;
  return (
    <div className="os-start-group">
      <h3 className="os-start-group-head">Recent</h3>
      {recent.map((v) => (
        <StartItem key={v.id} view={v} onLaunch={onLaunch} />
      ))}
    </div>
  );
}

/** How many desktops the group lists before it folds the rest away. */
const DESKTOPS_SHOWN = 5;

/**
 * Desktops: switch between them, and manage them.
 *
 * This is the **home for workspace management** now that the top strip hides itself
 * on a floating desktop. The strip was the only surface that could rename, create,
 * reset and delete a workspace, so hiding it without moving management would have
 * left those verbs unreachable — the constraint that kept a switcher out of the
 * taskbar in the first place.
 *
 * It switches *and* manages, which is what the taskbar could never do — see
 * `Taskbar.tsx`, which states plainly that it carries no workspace switcher. (Three
 * comments in this file and its neighbours used to describe "the pips in the
 * taskbar" doing the switching. There are no pips; they were removed. The comments
 * outlived them, and on the floating desktop we boot into they were describing the
 * *only* switcher a user had.)
 *
 * **The list is presets first, then custom desktops.** It used to be `useWorkspaces()`
 * alone, which is the set of *persisted rows* — a preset becomes one only when it is
 * first opened. So on a clean install this group listed a single entry while the
 * comment below claimed it carried "well over a dozen", and every hand-designed
 * workspace was unreachable here. `WorkspaceTabs` has always merged the two; this
 * now merges them identically, and a preset already opened appears once, not twice.
 *
 * Two things keep it from swallowing the launcher, because every module that
 * declares a `frames:` preset contributes a desktop and there are well over a
 * dozen of them:
 *
 * - **The list is folded past `DESKTOPS_SHOWN`.** Expanding is one click and the
 *   fold never hides the *active* desktop, which is kept in the short list
 *   wherever it sorts — a switcher whose current position is off-screen reads as
 *   though you are on none of them.
 * - **Creating a desktop is one row, not two.** Tiled-vs-floating is a preference
 *   (`desktop.defaultMode`) and lives on the settings page next to the control
 *   that converts the desktop you are on; a launcher is not where a paradigm
 *   should be chosen. See `DesktopModeSection`.
 */
function DesktopsGroup({ onClose }: { onClose: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const { workspaces, activeId } = useWorkspaces();
  const { frame } = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const defaultMode = useSetting<string>(DEFAULT_DESKTOP_MODE_KEY) ?? 'tiling';
  const floats = defaultMode === 'floating';
  // Every row here switches, makes or manages a workspace; with workspaces off
  // (`desktop.workspaces`) there is one desktop and the group has nothing to offer.
  const workspacesOn = useSetting<boolean>(WORKSPACES_ENABLED_KEY) === true;
  if (!workspacesOn) return null;
  const presets = registry.framePresets;
  const presetIds = new Set(presets.map((p) => p.id));
  const run = (command: string) => {
    void registry.runCommand(command);
    onClose();
  };

  // Presets first (a preset is a desktop whether or not it has been opened yet),
  // then the custom ones — the same merge, in the same order, as `WorkspaceTabs`.
  // A preset already opened has a row too, so it is filtered out of the second
  // half or it would appear twice under two different names.
  const entries = [
    ...presets.map((p) => ({ id: p.id, name: p.name, glyph: p.icon ?? p.name[0] })),
    ...workspaces
      .filter((w) => !presetIds.has(w.id))
      .map((w) => ({ id: w.id, name: w.name, glyph: undefined as string | undefined })),
  ];

  const shown = expanded ? entries : entries.slice(0, DESKTOPS_SHOWN);
  // The active desktop is never folded away, even when it sorts past the cut.
  const active = entries.find((e) => e.id === activeId);
  const listed = active && !shown.includes(active) ? [...shown, active] : shown;
  const hidden = entries.length - listed.length;

  return (
    <div className="os-start-group">
      <h3 className="os-start-group-head">Desktops</h3>
      {listed.map((entry) => (
        <button
          key={entry.id}
          type="button"
          role="menuitem"
          className={`os-start-item${entry.id === activeId ? ' is-active' : ''}`}
          aria-current={entry.id === activeId}
          onClick={() => {
            registry.switchWorkspace(entry.id);
            onClose();
          }}
        >
          {/* The active desktop shows which paradigm it is running; the rest show
              their own preset glyph, which is how they are identified everywhere
              else (the tab strip, the home launcher). A custom desktop has none
              and keeps the neutral dot. */}
          <span className="os-start-icon" aria-hidden="true">
            {entry.id === activeId ? (frame.mode === 'tiling' ? '▦' : '❐') : (entry.glyph ?? '·')}
          </span>
          <span className="os-start-title">{entry.name}</span>
        </button>
      ))}
      {hidden > 0 && (
        <button
          type="button"
          role="menuitem"
          className="os-start-item os-start-more"
          onClick={() => setExpanded(true)}
        >
          <span className="os-start-icon" aria-hidden="true">
            ⋯
          </span>
          <span className="os-start-title">{hidden} more desktops</span>
        </button>
      )}
      {expanded && entries.length > DESKTOPS_SHOWN && (
        <button
          type="button"
          role="menuitem"
          className="os-start-item os-start-more"
          onClick={() => setExpanded(false)}
        >
          <span className="os-start-icon" aria-hidden="true">
            ⋯
          </span>
          <span className="os-start-title">Show fewer</span>
        </button>
      )}
      <button
        type="button"
        role="menuitem"
        className="os-start-item"
        onClick={() => run(floats ? 'workspace.newFloating' : 'workspace.new')}
      >
        <span className="os-start-icon" aria-hidden="true">
          {floats ? '❐' : '▦'}
        </span>
        {/* The kind is reported, not chosen: it comes from `desktop.defaultMode`,
            and saying which one you are about to get is the difference between a
            preference and a surprise. */}
        <span className="os-start-title">New {floats ? 'floating' : 'tiled'} desktop</span>
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

/**
 * A module's panes behind one row. Closed by default: the category is for
 * scanning, and a feature's four panes open in place when you reach for it.
 */
function StartFold({ fold, onLaunch }: { fold: ModuleFold; onLaunch: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const icon = resolveViewIcon(fold.views[0].id, fold.views[0].icon, fold.views[0].title);
  return (
    <>
      <button
        type="button"
        role="menuitem"
        aria-expanded={open}
        className="os-start-item os-start-fold"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="os-start-icon" aria-hidden="true">
          {icon}
        </span>
        <span className="os-start-title">{fold.label}</span>
        <span className="os-start-fold-count">{fold.views.length}</span>
        <svg
          className={`os-start-fold-chevron${open ? ' is-open' : ''}`}
          width="10"
          height="10"
          viewBox="0 0 10 10"
          aria-hidden="true"
        >
          <path d="M3.5 2 7 5 3.5 8" fill="none" stroke="currentColor" strokeWidth="1.3" />
        </svg>
      </button>
      {open && (
        <div className="os-start-fold-body">
          {fold.views.map((v) => (
            <StartItem key={v.id} view={v} onLaunch={onLaunch} />
          ))}
        </div>
      )}
    </>
  );
}
