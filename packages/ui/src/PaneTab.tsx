import { useEffect, useRef, useState, type CSSProperties, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { DockviewDefaultTab, type IDockviewPanelHeaderProps } from 'dockview';
import { registry } from '@horrible/core';

/**
 * Custom workspace tab: the default dockview tab (title + dirty state) plus a ▾
 * button that swaps the pane's content to any other registered panel/widget in
 * place — `registry.layoutController.changePaneType` keeps the same instance id, so
 * geometry and autosave are undisturbed. We hide dockview's built-in close and
 * render our own so the order reads `title ▾ ✕`. See docs/architecture/windowing.md.
 */
interface PaneOption {
  id: string;
  title: string;
  kind: 'Panel' | 'Widget';
}

function paneOptions(): PaneOption[] {
  const companionIds = new Set(registry.panelGroups.flatMap((g) => g.companions.map((c) => c.id)));
  return [
    ...registry.panels.map((p) => ({ id: p.id, title: p.title, kind: 'Panel' as const })),
    ...registry.widgets
      .filter((w) => !companionIds.has(w.id))
      .map((w) => ({ id: w.id, title: w.title, kind: 'Widget' as const })),
  ].sort((a, b) => a.title.localeCompare(b.title));
}

/** Min space (px) the menu wants below the tab before it flips upward instead. */
const FLIP_THRESHOLD = 220;

/** Substring match on title or id — the pane catalog is small, so the simple
 * `includes` filter the command palette uses is plenty. `kind` is deliberately
 * excluded: "Panel"/"Widget" share common letters, so including it made short
 * queries match everything. */
function filterOptions(options: PaneOption[], query: string): PaneOption[] {
  const q = query.trim().toLowerCase();
  if (!q) return options;
  return options.filter((o) => `${o.title} ${o.id}`.toLowerCase().includes(q));
}

export function PaneTab(props: IDockviewPanelHeaderProps) {
  const [open, setOpen] = useState(false);
  // Positioned by the trigger's viewport rect. We anchor to `top` (opening down)
  // or `bottom` (flipped up) and cap `maxHeight` to the available space so a long
  // list never runs off-screen — it scrolls within whatever room the tab has.
  const [menuStyle, setMenuStyle] = useState<CSSProperties>({});
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const btnRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const currentId = (props.params as { panelId?: string })?.panelId;

  const matches = filterOptions(paneOptions(), query);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: globalThis.MouseEvent) => {
      const t = e.target as Element;
      if (btnRef.current?.contains(t) || t.closest?.('.pane-tab-menu')) return;
      setOpen(false);
    };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [open]);

  // Focus the filter input on open so the user can type immediately.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Keep the highlighted row visible as the selection moves through a long list.
  useEffect(() => {
    menuRef.current
      ?.querySelector('.pane-tab-menu-item.selected')
      ?.scrollIntoView({ block: 'nearest' });
  }, [selected, query]);

  const toggle = (e: MouseEvent) => {
    e.stopPropagation();
    const rect = btnRef.current?.getBoundingClientRect();
    if (rect) {
      const margin = 8;
      const spaceBelow = window.innerHeight - rect.bottom - margin;
      const spaceAbove = rect.top - margin;
      const flipUp = spaceBelow < FLIP_THRESHOLD && spaceAbove > spaceBelow;
      setMenuStyle({
        left: rect.left,
        ...(flipUp
          ? { bottom: window.innerHeight - rect.top + 2, maxHeight: spaceAbove }
          : { top: rect.bottom + 2, maxHeight: spaceBelow }),
      });
    }
    setQuery('');
    setSelected(0);
    setOpen((v) => !v);
  };

  const pick = (id: string) => {
    setOpen(false);
    if (id !== currentId) registry.layoutController?.changePaneType(props.api.id, id);
  };

  const onInputKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setOpen(false);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelected((s) => Math.min(s + 1, matches.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelected((s) => Math.max(s - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const choice = matches[selected];
      if (choice) pick(choice.id);
    }
  };

  return (
    <div className="pane-tab">
      <DockviewDefaultTab {...props} hideClose />
      <button
        ref={btnRef}
        className="pane-tab-btn"
        title="Change pane type"
        onClick={toggle}
        onMouseDown={(e) => e.stopPropagation()}
      >
        ▾
      </button>
      <button
        className="pane-tab-close"
        title="Close"
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          props.api.close();
        }}
      >
        ✕
      </button>
      {open &&
        createPortal(
          <div ref={menuRef} className="pane-tab-menu" style={menuStyle}>
            <input
              ref={inputRef}
              className="pane-tab-menu-search"
              value={query}
              placeholder="Filter panes…"
              onChange={(e) => {
                setQuery(e.target.value);
                setSelected(0);
              }}
              onKeyDown={onInputKey}
              onMouseDown={(e) => e.stopPropagation()}
            />
            {matches.map((o, i) => {
              const cls = [
                'pane-tab-menu-item',
                o.id === currentId ? 'active' : '',
                i === selected ? 'selected' : '',
              ]
                .filter(Boolean)
                .join(' ');
              return (
                <button
                  key={o.id}
                  className={cls}
                  onClick={() => pick(o.id)}
                  onMouseEnter={() => setSelected(i)}
                >
                  <span className="pane-tab-menu-title">{o.title}</span>
                  <span className="pane-tab-menu-kind">{o.kind}</span>
                </button>
              );
            })}
            {matches.length === 0 && <div className="pane-tab-menu-empty">No matching panes</div>}
          </div>,
          document.body,
        )}
    </div>
  );
}
