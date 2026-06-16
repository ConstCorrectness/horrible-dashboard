/**
 * Python REPL module: an embedded interpreter pane whose `dash` SDK scripts the
 * dashboard. Non-singleton (open as many consoles as you like, tab/split/float
 * them), bottom dock by default — mirroring the terminal. The backend kernel runs
 * per `/ws` connection (backend/modules/repl). See docs/modules/repl.md.
 */
import { registry, type ModuleManifest } from '../../registry';
import { ReplPane } from './ReplPane';

export const replModule: ModuleManifest = {
  id: 'repl',
  title: 'Python REPL',
  panels: [
    {
      id: 'repl.console',
      title: 'Python REPL',
      component: ReplPane,
      defaultPlacement: 'bottom',
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
