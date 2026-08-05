/**
 * The one context menu. Mounted once by `AppShell`; every right-click anywhere in
 * the app routes through `openContextMenu` and lands here.
 *
 * What it centralises, beyond not writing the same `<div>` four times:
 *
 * - **Placement.** Measure first, then position with `placeLayer`, so a menu near
 *   an edge flips or clamps against its *real* size. The menus this replaces
 *   either did not clamp at all (the activity rail placed at the raw `clientX`/
 *   `clientY`) or clamped against a hardcoded guess that stopped being true as
 *   soon as an item was added.
 * - **Escape.** Registered on the shared transient stack, so Escape closes the
 *   menu through the shell's one ordered ladder instead of a fifth `window`
 *   keydown listener racing the other four.
 * - **Keyboard.** Roving focus with arrows/Home/End/Enter, and submenus that open
 *   with ArrowRight and close with ArrowLeft. Keys are handled on the focused menu
 *   element, never on `window`.
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { createPortal } from 'react-dom';
import {
  closeContextMenu,
  contextMenuStore,
  placeLayer,
  registerTransient,
  type ContextMenuItem,
  type Placement,
  type Rect,
} from '@horrible/core';

/** Menus start here while being measured — off-screen, not `display:none`, or
 *  they would measure as zero. */
const MEASURING: Placement = { left: -9999, top: -9999, side: 'bottom' };

function useViewport(): { width: number; height: number } {
  const [size, setSize] = useState(() => ({
    width: typeof window === 'undefined' ? 0 : window.innerWidth,
    height: typeof window === 'undefined' ? 0 : window.innerHeight,
  }));
  useEffect(() => {
    const onResize = () => setSize({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return size;
}

interface PanelProps {
  anchor: Rect;
  items: ContextMenuItem[][];
  side?: 'bottom' | 'right';
  /** Close the whole menu tree, not just this panel. */
  onDismiss: () => void;
  /** Submenus return focus to their parent item on ArrowLeft. */
  onCloseSelf?: () => void;
  autoFocus?: boolean;
}

function MenuPanel({
  anchor,
  items,
  side = 'bottom',
  onDismiss,
  onCloseSelf,
  autoFocus = true,
}: PanelProps) {
  const ref = useRef<HTMLDivElement>(null);
  const viewport = useViewport();
  const [placement, setPlacement] = useState<Placement>(MEASURING);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [openSubmenu, setOpenSubmenu] = useState<{ index: number; anchor: Rect } | null>(null);

  // Flattened, in render order, so keyboard nav is a single index across groups.
  const flat = items.flat();
  const focusable = flat.map((item, i) => (item.disabled ? -1 : i)).filter((i) => i >= 0);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setPlacement(
      placeLayer({
        anchor,
        content: { width: rect.width, height: rect.height },
        viewport,
        side,
        offset: side === 'right' ? -4 : 0,
        padding: 6,
        shrink: true,
      }),
    );
    // `flat.length` is in the deps because a submenu can change size between
    // renders; re-measuring is cheap and a stale placement is visible.
  }, [anchor, viewport, side, flat.length]);

  useEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus]);

  const activate = (item: ContextMenuItem) => {
    if (item.disabled) return;
    if (item.submenu?.length) return;
    // Close before running: an action that opens a dialog must not fight the menu
    // for focus, and one that unmounts its own pane would otherwise leave the menu
    // anchored to nothing.
    onDismiss();
    void item.run();
  };

  const move = (delta: number) => {
    if (!focusable.length) return;
    const at = focusable.indexOf(activeIndex);
    const next =
      at < 0
        ? delta > 0
          ? 0
          : focusable.length - 1
        : (at + delta + focusable.length) % focusable.length;
    setActiveIndex(focusable[next]);
    setOpenSubmenu(null);
  };

  const openSubmenuAt = (index: number, el: HTMLElement | null) => {
    if (!el) return;
    const r = el.getBoundingClientRect();
    setOpenSubmenu({ index, anchor: { x: r.x, y: r.y, width: r.width, height: r.height } });
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        move(1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        move(-1);
        break;
      case 'Home':
        e.preventDefault();
        setActiveIndex(focusable[0] ?? -1);
        break;
      case 'End':
        e.preventDefault();
        setActiveIndex(focusable[focusable.length - 1] ?? -1);
        break;
      case 'ArrowRight': {
        const item = flat[activeIndex];
        if (item?.submenu?.length) {
          e.preventDefault();
          openSubmenuAt(
            activeIndex,
            ref.current?.querySelector(`[data-index="${activeIndex}"]`) ?? null,
          );
        }
        break;
      }
      case 'ArrowLeft':
        if (onCloseSelf) {
          e.preventDefault();
          onCloseSelf();
        }
        break;
      case 'Enter':
      case ' ': {
        const item = flat[activeIndex];
        if (!item) break;
        e.preventDefault();
        if (item.submenu?.length) {
          openSubmenuAt(
            activeIndex,
            ref.current?.querySelector(`[data-index="${activeIndex}"]`) ?? null,
          );
        } else {
          activate(item);
        }
        break;
      }
      default:
        break;
    }
  };

  let index = -1;
  return (
    <>
      <div
        ref={ref}
        className="ctx-menu"
        role="menu"
        tabIndex={-1}
        onKeyDown={onKeyDown}
        style={{
          left: placement.left,
          top: placement.top,
          ...(placement.maxHeight !== undefined ? { maxHeight: placement.maxHeight } : {}),
        }}
        // A right-click *inside* the menu should not open a second one.
        onContextMenu={(e) => e.preventDefault()}
      >
        {items.map((group, gi) => (
          <div className="ctx-menu-group" key={gi}>
            {gi > 0 && <div className="ctx-menu-sep" role="separator" />}
            {group.map((item) => {
              index += 1;
              const i = index;
              const hasSub = !!item.submenu?.length;
              return (
                <button
                  key={item.id}
                  type="button"
                  role="menuitem"
                  data-index={i}
                  className={[
                    'ctx-menu-item',
                    item.danger ? 'is-danger' : '',
                    item.checked ? 'is-checked' : '',
                    i === activeIndex ? 'is-active' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  disabled={item.disabled}
                  aria-haspopup={hasSub || undefined}
                  aria-checked={item.checked}
                  onMouseEnter={(e) => {
                    setActiveIndex(i);
                    if (hasSub) openSubmenuAt(i, e.currentTarget);
                    else setOpenSubmenu(null);
                  }}
                  onClick={(e) => {
                    if (hasSub) openSubmenuAt(i, e.currentTarget);
                    else activate(item);
                  }}
                >
                  <span className="ctx-menu-check">{item.checked ? '✓' : ''}</span>
                  <span className="ctx-menu-label">{item.label}</span>
                  {item.hint && <span className="ctx-menu-hint">{item.hint}</span>}
                  {hasSub && <span className="ctx-menu-more">›</span>}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      {openSubmenu && flat[openSubmenu.index]?.submenu?.length ? (
        <MenuPanel
          anchor={openSubmenu.anchor}
          items={[flat[openSubmenu.index].submenu!]}
          side="right"
          onDismiss={onDismiss}
          onCloseSelf={() => {
            setOpenSubmenu(null);
            ref.current?.focus();
          }}
        />
      ) : null}
    </>
  );
}

/** Mounted once by the shell. Renders nothing until something is right-clicked. */
export function ContextMenuLayer() {
  const open = useSyncExternalStore(contextMenuStore.subscribe, contextMenuStore.getSnapshot);
  const dismiss = useCallback(() => closeContextMenu(), []);

  // Escape goes through the shell's ladder, so the menu closes before (say) an
  // area's fullscreen does.
  useEffect(() => {
    if (!open) return;
    return registerTransient(dismiss);
  }, [open, dismiss]);

  // Any press outside the menu tree closes it. `pointerdown` rather than `click`
  // so the menu is gone before the underlying surface reacts to the press.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!(e.target as Element | null)?.closest?.('.ctx-menu')) dismiss();
    };
    // Scrolling the surface under a menu leaves it pointing at the wrong row.
    const onScroll = () => dismiss();
    window.addEventListener('pointerdown', onDown, true);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      window.removeEventListener('pointerdown', onDown, true);
      window.removeEventListener('scroll', onScroll, true);
    };
  }, [open, dismiss]);

  if (!open) return null;
  return createPortal(
    <MenuPanel
      anchor={{ x: open.x, y: open.y, width: 0, height: 0 }}
      items={open.groups}
      onDismiss={dismiss}
    />,
    document.body,
  );
}
