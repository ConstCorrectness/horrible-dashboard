import { lazy, type ComponentType } from 'react';

/**
 * A pane body loaded on first render instead of at boot.
 *
 * Every manifest is registered before the shell paints, so a component imported
 * statically by a manifest is parsed at startup whether or not its pane is ever
 * opened — which is how three.js, Rapier, Agora, pdf.js and CodeMirror all ended
 * up in the boot path. `PaneHost` already renders bodies under `<Suspense>`, so a
 * lazy component needs nothing else from the host.
 *
 * The file behind `load` must not *also* be imported statically from anything on
 * the boot path (a barrel re-export counts): the bundler then has nothing to
 * split, and the pane silently stays eager.
 */
export function lazyPane<M, K extends keyof M>(load: () => Promise<M>, name: K): ComponentType {
  return lazy(async () => ({ default: (await load())[name] as ComponentType }));
}
