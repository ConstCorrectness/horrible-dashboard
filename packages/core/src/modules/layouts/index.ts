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
      id: 'research',
      name: 'Research',
      icon: '🔬',
      // Discovery on the left (console/arxiv/browser tab together), reading on
      // the right (viewers above the library) — opening a paper never steals
      // the console. The researcher agent docks right, one keystroke away.
      frame: {
        center: {
          split: 'row',
          sizes: [0.58, 0.42],
          children: [
            {
              tabs: ['research.console', 'research.arxiv', 'browser.view'],
              active: 0,
            },
            {
              split: 'column',
              sizes: [0.65, 0.35],
              children: [
                { tabs: ['research.pdfViewer', 'research.pageViewer'] },
                { pane: 'library.panel' },
              ],
            },
          ],
        },
        docks: {
          right: { tools: ['agent.chat'], size: 360 },
          bottom: { tools: ['observability.io'], visible: false },
        },
      },
    },
    {
      id: 'dataops',
      name: 'Data Ops',
      icon: '🗄',
      // Console left, knowledge right; the scratchpad (REPL) and the live I/O feed
      // share the bottom dock because you reach for exactly one of them at a time.
      agent: 'dba',
      frame: {
        center: {
          split: 'row',
          sizes: [0.65, 0.35],
          children: [{ tabs: ['database.console'] }, { pane: 'library.panel' }],
        },
        docks: {
          right: { tools: ['agent.chat'], size: 380 },
          bottom: {
            tools: ['repl.console', 'observability.io'],
            activeTool: 'repl.console',
          },
        },
      },
    },
    {
      id: 'webops',
      name: 'Web Ops',
      icon: '🌐',
      // Reading the live web, not papers: the browser pane brings its own network
      // region (Waterfall/DNS/Route) along, which is what separates this from
      // Research — search feeds it from the left, what you keep lands right.
      agent: 'researcher',
      frame: {
        center: {
          split: 'row',
          sizes: [0.6, 0.4],
          children: [
            { tabs: ['browser.view'] },
            {
              split: 'column',
              sizes: [0.5, 0.5],
              children: [{ pane: 'library.panel' }, { pane: 'research.pageViewer' }],
            },
          ],
        },
        docks: {
          left: { tools: ['search.panel'], size: 300 },
          right: { tools: ['agent.chat'], size: 360 },
          bottom: { tools: ['observability.io'], visible: false },
        },
      },
    },
    {
      id: 'harness',
      name: 'Coding Harnesses',
      icon: '🛠',
      // The Games pane carries the whole loop (build → board → play) as internal
      // sections, so the center is just that pane; the log and the episode
      // trajectory are what you watch beside it while your agent plays.
      frame: {
        center: {
          split: 'row',
          sizes: [0.65, 0.35],
          children: [
            { pane: 'games.lobby' },
            {
              split: 'column',
              children: [{ pane: 'games.log' }, { pane: 'games.episodes' }],
            },
          ],
        },
        docks: {
          left: {
            tools: ['games.ladder', 'games.replays', 'games.players', 'games.profile'],
            activeTool: 'games.ladder',
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
