import { useEffect, useRef, useState, type MouseEvent } from 'react';
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
  return [
    ...registry.panels.map((p) => ({ id: p.id, title: p.title, kind: 'Panel' as const })),
    ...registry.widgets.map((w) => ({ id: w.id, title: w.title, kind: 'Widget' as const })),
  ].sort((a, b) => a.title.localeCompare(b.title));
}

export function PaneTab(props: IDockviewPanelHeaderProps) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);
  const currentId = (props.params as { panelId?: string })?.panelId;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: globalThis.MouseEvent) => {
      const t = e.target as Element;
      if (btnRef.current?.contains(t) || t.closest?.('.pane-tab-menu')) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const toggle = (e: MouseEvent) => {
    e.stopPropagation();
    const rect = btnRef.current?.getBoundingClientRect();
    if (rect) setMenuPos({ x: rect.left, y: rect.bottom + 2 });
    setOpen((v) => !v);
  };

  const pick = (id: string) => {
    setOpen(false);
    if (id !== currentId) registry.layoutController?.changePaneType(props.api.id, id);
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
          <div className="pane-tab-menu" style={{ left: menuPos.x, top: menuPos.y }}>
            {paneOptions().map((o) => (
              <button
                key={o.id}
                className={o.id === currentId ? 'pane-tab-menu-item active' : 'pane-tab-menu-item'}
                onClick={() => pick(o.id)}
              >
                <span className="pane-tab-menu-title">{o.title}</span>
                <span className="pane-tab-menu-kind">{o.kind}</span>
              </button>
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}
