/**
 * Workflow presets: the predefined Blender-style workspaces shown in the top
 * tab strip. Each preset seeds a stable-id workspace (`dashboard`, `scripting`,
 * …) as a **complete frame** (center area tree + docks) on first activation;
 * the user's rearrangements then persist per workspace, and `layout.reset`
 * restores the preset. These cross-cutting presets span several modules' panes,
 * so they live here rather than in any one feature module. See
 * docs/architecture/windowing.mdx.
 */
import { dialogs } from '../../dialogs';
import { toastsStore } from '../../toasts';
import { registry, type ModuleManifest } from '../../registry';

export const layoutsModule: ModuleManifest = {
  id: 'layouts',
  title: 'Layouts',
  frames: [
    {
      id: 'dashboard',
      name: 'Dashboard',
      icon: '▦',
      frame: {
        center: {
          split: 'row',
          sizes: [0.55, 0.45],
          children: [
            { pane: 'dashboard.welcome', headerCollapsed: true },
            {
              split: 'column',
              children: [
                { pane: 'dashboard.backendStatus', headerCollapsed: true },
                { pane: 'clubhouse.account', headerCollapsed: true },
              ],
            },
          ],
        },
        docks: {
          bottom: { tools: ['observability.io'], visible: false },
        },
      },
    },
    {
      id: 'scripting',
      name: 'Scripting',
      icon: '⌨',
      frame: {
        center: { pane: 'editor.buffer' },
        docks: {
          left: { tools: ['files.tree'], size: 260 },
          right: { tools: ['agent.chat'], visible: false },
          bottom: { tools: ['terminal.instance', 'repl.console'], activeTool: 'terminal.instance' },
        },
      },
    },
    {
      id: 'harness',
      name: 'Coding Harnesses',
      icon: '🛠',
      frame: {
        center: {
          split: 'row',
          sizes: [0.35, 0.65],
          children: [
            { pane: 'games.loadout' },
            {
              split: 'row',
              sizes: [0.65, 0.35],
              children: [
                { pane: 'games.board' },
                { pane: 'games.thoughts' },
              ],
            },
          ],
        },
        docks: {
          left: {
            tools: ['games.lobby', 'games.ladder', 'games.replays', 'games.players', 'games.profile'],
            activeTool: 'games.lobby',
            size: 280,
          },
        },
      },
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
