/**
 * Render an **anywidget** view. Unlike the core ipywidgets (native controls in
 * WidgetView), an anywidget ships its own front-end: the model state carries an
 * `_esm` ES-module string (exporting `render({ model, el })`, optionally
 * `initialize`) and optional `_css`. We execute that module in the browser and
 * hand it a Backbone-flavoured **model shim** backed by the session WidgetManager,
 * so trait reads/writes and custom messages ride the same comm as everything else.
 *
 * This is the pragmatic "arbitrary widget" story: anywidget is the modern way to
 * author custom Jupyter widgets. Classic third-party widgets that ship AMD view
 * bundles (`@jupyter-widgets/...`) are not loaded — they fall back to the compact
 * placeholder in WidgetView.
 *
 * Trusted-local posture: the `_esm` is code from the user's own kernel, same as
 * the HTML-output and plugin paths — we run it without a sandbox.
 */
import { useEffect, useRef, type ReactElement } from 'react';

import type { CustomMsg, WidgetManager, WidgetState } from './WidgetManager';

/** The subset of anywidget's front-end model that `render()` functions rely on. */
interface AnyModel {
  get(key: string): unknown;
  set(key: string, value: unknown): void;
  save_changes(): void;
  on(event: string, cb: (...args: unknown[]) => void): void;
  off(event?: string, cb?: (...args: unknown[]) => void): void;
  send(content: unknown, callbacks?: unknown, buffers?: ArrayBuffer[]): void;
}

type RenderCtx = { model: AnyModel; el: HTMLElement };
type Cleanup = void | (() => void);
type RenderFn = (ctx: RenderCtx) => Cleanup | Promise<Cleanup>;
interface AnyWidgetModule {
  default?: { initialize?: (ctx: { model: AnyModel }) => void; render?: RenderFn };
  render?: RenderFn;
  initialize?: (ctx: { model: AnyModel }) => void;
}

/** A tiny Backbone-style event bus for `change` / `change:<key>` / `msg:custom`. */
class Emitter {
  private map = new Map<string, Set<(...a: unknown[]) => void>>();
  on(ev: string, cb: (...a: unknown[]) => void): void {
    let s = this.map.get(ev);
    if (!s) this.map.set(ev, (s = new Set()));
    s.add(cb);
  }
  off(ev?: string, cb?: (...a: unknown[]) => void): void {
    if (!ev) return void this.map.clear();
    if (!cb) return void this.map.delete(ev);
    this.map.get(ev)?.delete(cb);
  }
  emit(ev: string, ...args: unknown[]): void {
    this.map.get(ev)?.forEach((cb) => cb(...args));
  }
}

async function loadModule(esm: string): Promise<AnyWidgetModule> {
  const url = URL.createObjectURL(new Blob([esm], { type: 'text/javascript' }));
  try {
    // @vite-ignore — runtime module string, never statically analyzable.
    return (await import(/* @vite-ignore */ url)) as AnyWidgetModule;
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function AnyWidgetView({
  manager,
  modelId,
}: {
  manager: WidgetManager;
  modelId: string;
}): ReactElement {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let disposed = false;
    let cleanup: (() => void) | undefined;
    const disposers: Array<() => void> = [];

    const emitter = new Emitter();
    const staged: WidgetState = {};
    const model: AnyModel = {
      get: (key) => manager.getState(modelId)?.[key],
      set: (key, value) => {
        staged[key] = value;
        manager.setLocal(modelId, { [key]: value });
      },
      save_changes: () => {
        if (Object.keys(staged).length === 0) return;
        manager.commit(modelId, { ...staged });
        for (const k of Object.keys(staged)) delete staged[k];
      },
      on: (ev, cb) => emitter.on(ev, cb),
      off: (ev, cb) => emitter.off(ev, cb),
      send: (content) => manager.sendCustom(modelId, content as Record<string, unknown>),
    };

    // Kernel → view: fan trait updates out as `change:<key>` + `change`.
    let prev = { ...(manager.getState(modelId) ?? {}) };
    disposers.push(
      manager.subscribe(modelId, () => {
        const next = manager.getState(modelId) ?? {};
        for (const key of Object.keys(next)) {
          if (next[key] !== prev[key]) emitter.emit(`change:${key}`, model, next[key]);
        }
        prev = { ...next };
        emitter.emit('change', model);
      }),
    );
    disposers.push(
      manager.subscribeCustom(modelId, (m: CustomMsg) =>
        emitter.emit('msg:custom', m.content, m.buffers),
      ),
    );

    const esm = String(manager.getState(modelId)?._esm ?? '');
    const css = manager.getState(modelId)?._css;
    if (typeof css === 'string' && css) {
      const style = document.createElement('style');
      style.textContent = css;
      host.appendChild(style);
      disposers.push(() => style.remove());
    }

    if (esm) {
      loadModule(esm)
        .then(async (mod) => {
          if (disposed) return;
          const init = mod.default?.initialize ?? mod.initialize;
          const render = mod.default?.render ?? mod.render;
          init?.({ model });
          const ret = await render?.({ model, el: host });
          if (disposed && typeof ret === 'function') ret();
          else if (typeof ret === 'function') cleanup = ret;
        })
        .catch((err: unknown) => {
          if (!disposed) host.append(errorBox(String(err)));
        });
    } else {
      host.append(errorBox('anywidget model has no _esm'));
    }

    return () => {
      disposed = true;
      cleanup?.();
      for (const d of disposers) d();
      host.replaceChildren();
    };
  }, [manager, modelId]);

  return <div ref={hostRef} className="anywidget" />;
}

function errorBox(msg: string): HTMLElement {
  const div = document.createElement('div');
  div.style.cssText =
    'color:var(--danger,#e5534b);font-size:0.75rem;font-family:var(--font-mono,monospace)';
  div.textContent = `anywidget error: ${msg}`;
  return div;
}
