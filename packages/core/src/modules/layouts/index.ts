/**
 * Workflow layouts: the predefined Blender-style workspaces shown in the shell
 * rail. Each preset seeds a stable-id workspace (`dashboard`, `scripting`, …) on
 * first activation; the user's rearrangements then persist per layout, and
 * `layout.reset` restores the preset. These cross-cutting presets span several
 * modules' panes, so they live here rather than in any one feature module. See
 * docs/architecture/windowing.md.
 */
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
      run: () => {
        const name = window.prompt('New workspace name', 'Workspace');
        if (name) void registry.layoutController?.createWorkspace(name);
      },
    },
    {
      id: 'workspace.delete',
      title: 'Workspace: Delete current',
      run: () => registry.layoutController?.deleteActiveWorkspace(),
    },
  ],
};
