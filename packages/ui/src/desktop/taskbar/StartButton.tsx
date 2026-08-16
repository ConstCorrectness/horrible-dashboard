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
 * - **It is grouped by pane role.** Sixty-odd entries in one alphabetical run
 *   told the user nothing about the model they were choosing from; Documents /
 *   Tools / Widgets is the same distinction the frame itself makes about where a
 *   pane will land. Searching flattens the groups, because a filtered list of
 *   four things does not need headings.
 * - **It has a settings footer.** The bottom-left corner is where people go for
 *   settings, and this menu previously offered it only as one row among sixty,
 *   sorted under S.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  openContextMenu,
  registry,
  type PaneRole,
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

/**
 * The three roles, in the order they appear, with the heading each gets.
 *
 * The wording says what the role *means to the user* rather than repeating the
 * enum: someone choosing from a launcher wants to know what the thing is, not
 * which field it declares. Order is by how often you reach for one.
 */
const ROLE_GROUPS: { role: PaneRole; label: string }[] = [
  { role: 'document', label: 'Documents' },
  { role: 'tool', label: 'Tools' },
  { role: 'widget', label: 'Widgets' },
];

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
    return views.filter((v) => v.title.toLowerCase().includes(q) || v.id.includes(q));
  }, [views, query]);

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
          : ROLE_GROUPS.map(({ role, label }) => {
              const group = matches.filter((v) => v.role === role);
              if (!group.length) return null;
              return (
                <div key={role} className="os-start-group">
                  <h3 className="os-start-group-head">{label}</h3>
                  {group.map((v) => (
                    <StartItem key={v.id} view={v} onLaunch={launch} />
                  ))}
                </div>
              );
            })}
        {!matches.length && <p className="os-start-empty">Nothing matches “{query}”.</p>}
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

function StartItem({ view, onLaunch }: { view: View; onLaunch: (id: string) => void }) {
  return (
    <button
      type="button"
      role="menuitem"
      className="os-start-item"
      onClick={() => onLaunch(view.id)}
    >
      <span className="os-start-icon" aria-hidden="true">
        {view.icon ?? view.title[0]}
      </span>
      <span>{view.title}</span>
    </button>
  );
}
