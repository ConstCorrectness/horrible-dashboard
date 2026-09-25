/**
 * Python REPL module: an embedded interpreter pane whose `dash` SDK scripts the
 * dashboard. Non-singleton (open as many consoles as you like, tab/split/float
 * them), bottom dock by default — mirroring the terminal. The backend kernel runs
 * per `/ws` connection (backend/modules/repl). See docs/modules/repl.md.
 */
import { lazyPane } from '../../lazy-pane';
import { registry, type ModuleManifest } from '../../registry';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const ReplPane = lazyPane(() => import('./ReplPane'), 'ReplPane');

export const replModule: ModuleManifest = {
  id: 'repl',
  title: 'Python REPL',
  category: 'build',
  panels: [
    {
      id: 'repl.console',
      title: 'Python REPL',
      component: ReplPane,
      role: 'tool',
      icon: '🐍',
      defaultDock: 'bottom',
      // Not a singleton: each open is its own kernel/namespace.
    },
  ],
  commands: [
    {
      id: 'repl.new',
      title: 'Python REPL: New console',
      run: () => registry.openPanel('repl.console'),
    },
  ],
};
