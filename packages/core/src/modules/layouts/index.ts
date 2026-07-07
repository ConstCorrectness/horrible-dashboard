/**
 * Workflow layouts: the predefined Blender-style workspaces shown in the shell
 * rail. Each preset seeds a stable-id workspace (`dashboard`, `scripting`, …) on
 * first activation; the user's rearrangements then persist per layout, and
 * `layout.reset` restores the preset. These cross-cutting presets span several
 * modules' panes, so they live here rather than in any one feature module. See
 * docs/architecture/windowing.md.
 */
import { dialogs } from '../../dialogs';
import { toastsStore } from '../../toasts';
import { registry, type ModuleManifest } from '../../registry';

export const layoutsModule: ModuleManifest = {
  id: 'layouts',
  title: 'Layouts',
  layouts: [
    {
      id: 'dashboard',
      name: 'Dashboard',
      icon: '▦',
      panes: [
        { id: 'dashboard.welcome' },
        {
          id: 'dashboard.backendStatus',
          position: { referencePanel: 'dashboard.welcome', direction: 'right' },
        },
        {
          id: 'observability.io',
          position: { referencePanel: 'dashboard.welcome', direction: 'below' },
        },
      ],
    },
    {
      id: 'scripting',
      name: 'Scripting',
      icon: '⌨',
      panes: [
        { id: 'files.tree' },
        {
          id: 'editor.buffer',
          position: { referencePanel: 'files.tree', direction: 'right' },
        },
        {
          id: 'repl.console',
          position: { referencePanel: 'editor.buffer', direction: 'below' },
        },
        {
          id: 'terminal.instance',
          position: { referencePanel: 'repl.console', direction: 'within' },
        },
      ],
    },
    {
      // Harness-engineering cockpit: author your agent's strategy + Python tools on
      // the left, watch it compete top-right, launch/join matches bottom-right. The
      // Agent Harness (games.loadout) and Game Board (games.board) are arcade-group
      // companions but seed here as standalone panes (a companion renders its bare
      // component when opened directly); games.lobby seeds as the arcade shell.
      id: 'harness',
      name: 'Coding Harnesses',
      icon: '🛠',
      panes: [
        { id: 'games.loadout' },
        {
          id: 'games.board',
          position: { referencePanel: 'games.loadout', direction: 'right' },
        },
        {
          id: 'games.lobby',
          position: { referencePanel: 'games.board', direction: 'below' },
        },
      ],
    },
  ],
  commands: [
    {
      id: 'layout.reset',
      title: 'Layout: Reset to preset',
      run: () => registry.layoutController?.resetLayout(),
    },
    {
      id: 'workspace.new',
      title: 'Workspace: New',
      run: async () => {
        const controller = registry.layoutController;
        if (!controller) {
          toastsStore.add(
            'warning',
            'Open a workspace first',
            'Switch to a workspace to add another.',
          );
          return;
        }
        const name = await dialogs.prompt({
          title: 'New workspace',
          placeholder: 'Workspace name',
          defaultValue: 'Workspace',
          confirmLabel: 'Create',
        });
        const trimmed = name?.trim();
        if (!trimmed) return;
        await controller.createWorkspace(trimmed);
        toastsStore.add('success', 'Workspace created', `“${trimmed}” is ready.`);
      },
    },
    {
      id: 'workspace.delete',
      title: 'Workspace: Delete current',
      run: () => registry.layoutController?.deleteActiveWorkspace(),
    },
  ],
};
