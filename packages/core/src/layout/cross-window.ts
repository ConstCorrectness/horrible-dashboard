/**
 * Shims that let main-realm code keep working on elements shown in a pop-out.
 *
 * ## ResizeObserver does not cross windows
 *
 * Measured in Electron 44: a `ResizeObserver` constructed in the main page **never
 * fires** for an element living in a child window's document — not even the
 * initial callback — while the child window's own `ResizeObserver` does.
 * (`IntersectionObserver` does cross.) Pane code, CodeMirror and the native
 * browser view all build theirs from the global, so a popped-out pane would stop
 * resizing with no error anywhere.
 *
 * So the global is replaced, once at boot, by a router: each `observe(el)` goes to
 * the `ResizeObserver` of the window `el` is in, and {@link rehomeObservers} moves
 * every observation whose element has since changed windows. Re-observing
 * delivers a fresh initial callback, which is exactly the "refit now" a moved pane
 * needs.
 *
 * ## Drag listeners on `window`
 *
 * A slider or splitter inside a pane typically listens on `window` (or `document`)
 * for `pointermove` / `pointerup` once a press starts. In a pop-out those events
 * fire on the child window, and the main-realm listener waits forever for its
 * pointer-up. {@link forwardPointerEvents} re-dispatches them on the main document.
 * Coordinates are the pop-out's, which is what that pane's own math expects.
 */

type ResizeObserverCtor = typeof ResizeObserver;

/** The window an element is observed from; null keeps the base realm's. */
export type RealmOf = (
  el: Element,
) => { ResizeObserver: ResizeObserverCtor; closed?: boolean } | null;

const routers = new Set<WeakRef<{ rehome(): void }>>();

/**
 * Build a `ResizeObserver` class that routes each target to its own realm's
 * observer. Exposed for tests; {@link installCrossWindowObservers} installs it.
 */
export function createRoutingResizeObserver(
  base: ResizeObserverCtor,
  realmOf: RealmOf,
): ResizeObserverCtor {
  type Realm = { ResizeObserver: ResizeObserverCtor; closed?: boolean };
  const BASE: Realm = { ResizeObserver: base };

  class RoutingResizeObserver {
    #callback: ResizeObserverCallback;
    #inner = new Map<Realm, ResizeObserver>();
    #targets = new Map<Element, { realm: Realm; options?: ResizeObserverOptions }>();

    constructor(callback: ResizeObserverCallback) {
      this.#callback = callback;
      routers.add(new WeakRef(this));
    }

    #observer(realm: Realm): ResizeObserver {
      let o = this.#inner.get(realm);
      if (!o) {
        o = new realm.ResizeObserver((entries) =>
          this.#callback(entries, this as unknown as ResizeObserver),
        );
        this.#inner.set(realm, o);
      }
      return o;
    }

    #realmFor(el: Element): Realm {
      const realm = realmOf(el);
      // The installing window's own `ResizeObserver` *is* this router: routing to
      // it would recurse forever. Its elements use the original it replaced.
      if (!realm || realm.closed || realm.ResizeObserver === RoutingResizeObserver) return BASE;
      return realm;
    }

    observe(el: Element, options?: ResizeObserverOptions): void {
      const realm = this.#realmFor(el);
      const prev = this.#targets.get(el);
      if (prev && prev.realm !== realm) this.#inner.get(prev.realm)?.unobserve(el);
      this.#targets.set(el, { realm, options });
      this.#observer(realm).observe(el, options);
    }

    unobserve(el: Element): void {
      const t = this.#targets.get(el);
      if (!t) return;
      this.#targets.delete(el);
      this.#inner.get(t.realm)?.unobserve(el);
    }

    disconnect(): void {
      for (const o of this.#inner.values()) o.disconnect();
      this.#inner.clear();
      this.#targets.clear();
    }

    /** Move observations whose element now lives in another window. */
    rehome(): void {
      for (const [el, t] of this.#targets) {
        const realm = this.#realmFor(el);
        if (realm === t.realm) continue;
        if (!t.realm.closed) this.#inner.get(t.realm)?.unobserve(el);
        t.realm = realm;
        this.#observer(realm).observe(el, t.options);
      }
      // A closed pop-out's observer has nothing left to watch.
      for (const [realm, o] of this.#inner) {
        if (realm.closed) {
          o.disconnect();
          this.#inner.delete(realm);
        }
      }
    }
  }

  return RoutingResizeObserver as unknown as ResizeObserverCtor;
}

/** Re-route every live observation after an element moved between windows. */
export function rehomeObservers(): void {
  for (const ref of routers) {
    const router = ref.deref();
    if (router) router.rehome();
    else routers.delete(ref);
  }
}

let installed = false;

/**
 * Replace `window.ResizeObserver` with the router. Call before anything builds an
 * observer (the app's entry point). Idempotent.
 */
export function installCrossWindowObservers(): void {
  if (installed || typeof window === 'undefined' || !window.ResizeObserver) return;
  installed = true;
  window.ResizeObserver = createRoutingResizeObserver(
    window.ResizeObserver,
    (el) => el.ownerDocument?.defaultView ?? null,
  );
}

/** Events that finish (or continue) a drag the main realm may be listening for. */
const FORWARDED = ['pointermove', 'pointerup', 'pointercancel', 'mousemove', 'mouseup'] as const;

/**
 * Re-dispatch drag events from pop-out `child` on the main document, so a
 * main-realm `window`/`document` listener sees them. Returns the detach.
 */
export function forwardPointerEvents(child: Window, main: Window = window): () => void {
  const forward = (e: Event) => {
    const src = e as PointerEvent;
    // A move with no button down is a hover, not a drag: forwarding it would
    // feed the main window's hover listeners another window's coordinates.
    if (src.type.endsWith('move') && src.buttons === 0) return;
    const Ctor = src.type.startsWith('pointer') ? PointerEvent : MouseEvent;
    const init: PointerEventInit = {
      bubbles: true,
      cancelable: true,
      clientX: src.clientX,
      clientY: src.clientY,
      screenX: src.screenX,
      screenY: src.screenY,
      movementX: src.movementX,
      movementY: src.movementY,
      button: src.button,
      buttons: src.buttons,
      ctrlKey: src.ctrlKey,
      shiftKey: src.shiftKey,
      altKey: src.altKey,
      metaKey: src.metaKey,
      pointerId: src.pointerId,
      pointerType: src.pointerType,
      isPrimary: src.isPrimary,
    };
    main.document.dispatchEvent(new Ctor(src.type, init));
  };
  for (const type of FORWARDED) child.addEventListener(type, forward);
  return () => {
    for (const type of FORWARDED) child.removeEventListener(type, forward);
  };
}
