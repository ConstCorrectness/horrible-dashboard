import { describe, expect, it } from 'vitest';

import { createRoutingResizeObserver, rehomeObservers } from '../cross-window';

/** A stand-in for one window's `ResizeObserver`: records what it watches. */
function fakeRealm(name: string) {
  const watching = new Set<object>();
  const instances: { fire(target: object): void }[] = [];
  class FakeRO {
    constructor(private cb: ResizeObserverCallback) {
      instances.push(this);
    }
    observe(el: object) {
      watching.add(el);
    }
    unobserve(el: object) {
      watching.delete(el);
    }
    disconnect() {
      watching.clear();
    }
    fire(target: object) {
      this.cb([{ target } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
    }
  }
  return {
    name,
    closed: false,
    ResizeObserver: FakeRO as unknown as typeof ResizeObserver,
    watching,
    instances,
  };
}

describe('routing ResizeObserver', () => {
  it('observes each element through the realm it lives in', () => {
    const main = fakeRealm('main');
    const child = fakeRealm('child');
    const home = new Map<object, ReturnType<typeof fakeRealm>>();
    const RO = createRoutingResizeObserver(main.ResizeObserver, (el) => home.get(el) ?? null);

    const a = {} as Element;
    const b = {} as Element;
    home.set(b, child);
    const seen: object[] = [];
    const ro = new RO((entries) => seen.push(entries[0].target));
    ro.observe(a);
    ro.observe(b);

    expect(main.watching.has(a)).toBe(true);
    expect(child.watching.has(b)).toBe(true);
    expect(main.watching.has(b)).toBe(false);

    child.instances[0].fire(b);
    expect(seen).toEqual([b]);
  });

  it('passes the router, not the inner observer, to the callback', () => {
    const main = fakeRealm('main');
    const RO = createRoutingResizeObserver(main.ResizeObserver, () => null);
    let got: unknown = null;
    const ro = new RO((_e, observer) => (got = observer));
    ro.observe({} as Element);
    main.instances[0].fire({});
    expect(got).toBe(ro);
  });

  it('rehomes an element that moved to a pop-out, and back', () => {
    const main = fakeRealm('main');
    const child = fakeRealm('child');
    const home = new Map<object, ReturnType<typeof fakeRealm>>();
    const RO = createRoutingResizeObserver(main.ResizeObserver, (el) => home.get(el) ?? null);
    const el = {} as Element;
    const ro = new RO(() => {});
    ro.observe(el);

    home.set(el, child);
    rehomeObservers();
    expect(main.watching.has(el)).toBe(false);
    expect(child.watching.has(el)).toBe(true);

    home.delete(el);
    rehomeObservers();
    expect(main.watching.has(el)).toBe(true);
    expect(child.watching.has(el)).toBe(false);
  });

  it('sends the installing window’s own elements to the original, not to itself', () => {
    // Installed, `window.ResizeObserver` is the router, so the main window's realm
    // names the router — routing there recursed until the stack blew.
    const main = fakeRealm('main');
    const self: { ResizeObserver: typeof ResizeObserver } = { ResizeObserver: main.ResizeObserver };
    const RO = createRoutingResizeObserver(main.ResizeObserver, () => self);
    self.ResizeObserver = RO;
    const el = {} as Element;
    new RO(() => {}).observe(el);
    expect(main.watching.has(el)).toBe(true);
  });

  it('falls back to the base realm when the element’s window has closed', () => {
    const main = fakeRealm('main');
    const child = fakeRealm('child');
    child.closed = true;
    const RO = createRoutingResizeObserver(main.ResizeObserver, () => child);
    const el = {} as Element;
    new RO(() => {}).observe(el);
    expect(main.watching.has(el)).toBe(true);
  });

  it('unobserve and disconnect reach every realm', () => {
    const main = fakeRealm('main');
    const child = fakeRealm('child');
    const home = new Map<object, ReturnType<typeof fakeRealm>>();
    const RO = createRoutingResizeObserver(main.ResizeObserver, (el) => home.get(el) ?? null);
    const a = {} as Element;
    const b = {} as Element;
    home.set(b, child);
    const ro = new RO(() => {});
    ro.observe(a);
    ro.observe(b);
    ro.unobserve(b);
    expect(child.watching.size).toBe(0);
    ro.disconnect();
    expect(main.watching.size).toBe(0);
  });
});
