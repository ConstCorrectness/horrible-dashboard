/**
 * How windows move: open, close, minimize, restore, and every change of geometry
 * that is not the pointer's (maximize, snap, restore-down, tiling a pair).
 *
 * The aim is the feel of a native window manager: short, decisive, decelerating.
 * Entrances settle fast (an expo-out curve, ~180ms), exits get out of the way
 * faster still (~130ms, accelerating), and geometry changes slide rather than
 * jump. Everything here is the Web Animations API on the window's own element:
 * no state, no re-render, nothing to clean up, and a later animation simply
 * replaces an earlier one.
 *
 * Geometry is animated by translate, an `inset()` clip and opacity (measure
 * before and after, play the difference back), never by left/top/width/height —
 * and never by `scale()`, which distorts the content (see `animateGeometry`). A
 * window's body can hold a canvas, a terminal or a browser engine, and resizing
 * those per frame would cost far more than a composited transform.
 *
 * Off under `prefers-reduced-motion` and with the `desktop.animations` setting
 * off; every function is then a no-op and the state simply changes.
 */
import { getSetting, settingsStore } from '@horrible/core';

/** The setting that turns window animation off. */
export const ANIMATIONS_SETTING_KEY = 'desktop.animations';

/** Mirrors `--dur-enter` / `--dur-exit` / `--dur-move` and the curves in themes.css. */
export const WINDOW_MOTION = {
  enter: 180,
  exit: 130,
  move: 220,
  /** Decelerate hard: most of the distance in the first third. */
  easeOut: 'cubic-bezier(0.16, 1, 0.3, 1)',
  /** Accelerate away: an exit should not linger at its start. */
  easeIn: 'cubic-bezier(0.4, 0, 1, 1)',
} as const;

/** The layout box, ignoring transforms (a running animation must not skew it). */
export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function motionEnabled(): boolean {
  if (typeof window === 'undefined' || typeof Element === 'undefined') return false;
  if (typeof Element.prototype.animate !== 'function') return false;
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return false;
  return getSetting<boolean>(ANIMATIONS_SETTING_KEY) !== false;
}

export function layoutBox(el: HTMLElement): Box {
  return { x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight };
}

/** Whether two boxes differ by more than a pixel anywhere. */
export function boxesDiffer(a: Box, b: Box): boolean {
  return (
    Math.abs(a.x - b.x) > 1 ||
    Math.abs(a.y - b.y) > 1 ||
    Math.abs(a.w - b.w) > 1 ||
    Math.abs(a.h - b.h) > 1
  );
}

/** A new window settles into place. */
export function animateWindowIn(el: HTMLElement): void {
  if (!motionEnabled()) return;
  el.animate(
    [
      { opacity: 0, transform: 'translateY(8px) scale(0.97)' },
      { opacity: 1, transform: 'none' },
    ],
    { duration: WINDOW_MOTION.enter, easing: WINDOW_MOTION.easeOut },
  );
}

/**
 * A closing window shrinks slightly and fades. Resolves when it has finished, so
 * the close can remove it then; `fill: forwards` keeps it invisible for the frame
 * between the animation's end and the removal.
 */
export function animateWindowOut(el: HTMLElement): Promise<void> {
  if (!motionEnabled()) return Promise.resolve();
  const anim = el.animate(
    [
      { opacity: 1, transform: 'none' },
      { opacity: 0, transform: 'scale(0.96)' },
    ],
    { duration: WINDOW_MOTION.exit, easing: WINDOW_MOTION.easeIn, fill: 'forwards' },
  );
  el.style.pointerEvents = 'none';
  return anim.finished.then(
    () => undefined,
    () => undefined,
  );
}

/**
 * Where a minimized window goes to and comes back from: toward its taskbar button
 * when there is one on screen, else straight down.
 */
function towardTaskbar(el: HTMLElement, target: DOMRect | null): string {
  const box = el.getBoundingClientRect();
  if (!target || !box.width || !box.height) return 'translateY(48px) scale(0.6)';
  const dx = target.left + target.width / 2 - (box.left + box.width / 2);
  const dy = target.top + target.height / 2 - (box.top + box.height / 2);
  return `translate(${dx}px, ${dy}px) scale(0.2)`;
}

/** The taskbar button of a pane, if the taskbar is showing one. */
export function taskbarButtonRect(instanceId: string | undefined): DOMRect | null {
  if (!instanceId || typeof document === 'undefined') return null;
  const button = document.querySelector<HTMLElement>(
    `[data-taskbar-instance="${CSS.escape(instanceId)}"]`,
  );
  const rect = button?.getBoundingClientRect();
  return rect && rect.width > 0 ? rect : null;
}

/**
 * The window has just been minimized: its class has already moved it off screen,
 * so the animation draws it where it was (the effect overrides the class's
 * transform and visibility while it runs) and sends it to the taskbar.
 */
export function animateMinimize(el: HTMLElement, target: DOMRect | null): void {
  if (!motionEnabled()) return;
  // Measured without the class's off-screen transform, which would put the
  // window 200vh up and aim the flight from there.
  const prev = el.style.transform;
  el.style.transform = 'none';
  const to = towardTaskbar(el, target);
  el.style.transform = prev;
  el.animate(
    [
      { transform: 'none', opacity: 1, visibility: 'visible', contentVisibility: 'visible' },
      { transform: to, opacity: 0, visibility: 'visible', contentVisibility: 'visible' },
    ],
    { duration: WINDOW_MOTION.enter, easing: WINDOW_MOTION.easeIn },
  );
}

/** The window has just been restored: it flies back out of the taskbar. */
export function animateRestore(el: HTMLElement, from: DOMRect | null): void {
  if (!motionEnabled()) return;
  el.animate(
    [
      { transform: towardTaskbar(el, from), opacity: 0 },
      { transform: 'none', opacity: 1 },
    ],
    { duration: WINDOW_MOTION.enter + 40, easing: WINDOW_MOTION.easeOut },
  );
}

/**
 * When the desktop itself last changed size. Resizing the app rescales every
 * window's rect on every frame; sliding each of those would restart an animation
 * per frame and leave the windows trailing the edge, so geometry changes this
 * close to one are applied as they come.
 */
let viewportChangedAt = -Infinity;
const VIEWPORT_SETTLE_MS = 300;

/** The window layer reports each change of its own size here. */
export function noteViewportChange(): void {
  viewportChangedAt = performance.now();
}

/** `clip-path: inset()` that shows `box` of an element laid out at `frame`. */
export function insetFor(box: Box, frame: Box, radius: string): string {
  const top = box.y - frame.y;
  const left = box.x - frame.x;
  const right = frame.x + frame.w - (box.x + box.w);
  const bottom = frame.y + frame.h - (box.y + box.h);
  return `inset(${top}px ${right}px ${bottom}px ${left}px round ${radius})`;
}

/** The ghost frame a window's shrink is drawn with, if one is mid-flight. */
const ghosts = new WeakMap<HTMLElement, HTMLElement>();

/**
 * The window's geometry changed under it: travel from the old box to the new one.
 *
 * **Never by scaling the window.** The window is already laid out at its new
 * size when this runs, so a FLIP `scale()` draws that layout squashed back into
 * the old box — at a maximize, roughly 0.5 × 0.7 with different factors on each
 * axis, so text visibly stretches as it plays — and a restore-down scales a
 * small raster up to screen size, which is where the blocky look came from.
 * Instead:
 *
 * - **Growing or moving** (maximize, snap to a bigger zone): the real window
 *   slides from the old corner while a clip opens from the old size to the new.
 *   Content is drawn at 1:1 throughout and simply revealed.
 * - **Shrinking on either axis** (restore-down, snap to a smaller zone): a clip
 *   cannot draw outside the element, so a plain frame — no content, nothing to
 *   distort — closes from the old box onto the new one while the window fades in
 *   over it.
 *
 * Both are transform/opacity plus an `inset()` clip: no layout and no repaint of
 * the window's content per frame.
 */
export function animateGeometry(el: HTMLElement, from: Box, to: Box): void {
  if (!motionEnabled() || !boxesDiffer(from, to)) return;
  if (performance.now() - viewportChangedAt < VIEWPORT_SETTLE_MS) return;
  ghosts.get(el)?.remove();
  const timing = { duration: WINDOW_MOTION.move, easing: WINDOW_MOTION.easeOut };
  const radius = getComputedStyle(el).borderTopLeftRadius || '0px';

  if (to.w >= from.w && to.h >= from.h) {
    // The old box at the new box's corner, then translated back to where it was.
    const shown = { x: to.x, y: to.y, w: from.w, h: from.h };
    el.animate(
      [
        {
          transform: `translate(${from.x - to.x}px, ${from.y - to.y}px)`,
          clipPath: insetFor(shown, to, radius),
        },
        { transform: 'none', clipPath: insetFor(to, to, radius) },
      ],
      timing,
    );
    return;
  }

  const parent = el.parentElement;
  if (!parent) return;
  const union: Box = {
    x: Math.min(from.x, to.x),
    y: Math.min(from.y, to.y),
    w: Math.max(from.x + from.w, to.x + to.w) - Math.min(from.x, to.x),
    h: Math.max(from.y + from.h, to.y + to.h) - Math.min(from.y, to.y),
  };
  const ghost = document.createElement('div');
  ghost.className = 'os-window-ghost';
  ghost.setAttribute('aria-hidden', 'true');
  Object.assign(ghost.style, {
    left: `${union.x}px`,
    top: `${union.y}px`,
    width: `${union.w}px`,
    height: `${union.h}px`,
    zIndex: el.style.zIndex,
  });
  // Before the window, at the same z: document order keeps the window on top.
  parent.insertBefore(ghost, el);
  ghosts.set(el, ghost);
  const done = () => {
    ghost.remove();
    if (ghosts.get(el) === ghost) ghosts.delete(el);
  };
  ghost
    .animate(
      [
        { clipPath: insetFor(from, union, radius), opacity: 1 },
        { clipPath: insetFor(to, union, radius), opacity: 1, offset: 0.7 },
        { clipPath: insetFor(to, union, radius), opacity: 0 },
      ],
      timing,
    )
    .finished.then(done, done);
  el.animate([{ opacity: 0 }, { opacity: 0, offset: 0.3 }, { opacity: 1 }], timing);
}

/**
 * Mirror the setting onto `<html data-motion="on|off">`, so the stylesheet's own
 * animations (a pane's content settling in on a tab switch) obey it too. The
 * reduced-motion media query is the stylesheet's to check; this is only the
 * setting. Returns the unsubscriber.
 */
export function syncMotionAttribute(): () => void {
  const apply = () => {
    document.documentElement.dataset.motion =
      getSetting<boolean>(ANIMATIONS_SETTING_KEY) === false ? 'off' : 'on';
  };
  apply();
  return settingsStore.subscribe(apply);
}
