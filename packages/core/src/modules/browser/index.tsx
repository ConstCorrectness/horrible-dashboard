import { registry, type ModuleManifest } from '../../registry';
import { browserAgentTools } from './agentTools';
import { BrowserPanel, focusActiveUrlBar } from './panels/BrowserPanel';
import { NetworkStrip } from './panels/NetworkStrip';

/**
 * The **browser** module: a dockable pane that renders web pages inline via an
 * `<iframe>`, with a URL bar, per-pane back/forward history, bookmarks/history
 * persisted server-side, a server-side reader mode for sites that refuse framing,
 * and (desktop only) a native-window pop-out. Works in both the web and desktop
 * builds; the pop-out is capability-gated (`browser.nativeWindow`). The agent can
 * read/open the web through the panel's `agentTools` (`browser.read`/`browser.open`).
 * See docs/modules/browser.mdx.
 */
export const browserModule: ModuleManifest = {
  id: 'browser',
  title: 'Browser',
  panels: [
    {
      id: 'browser.view',
      title: 'Browser',
      component: BrowserPanel,
      role: 'document',
      icon: '🌐',
      // Non-singleton: open as many browser tabs as you like.
      // The agent reads/opens the web through these (see agentTools.ts).
      agentTools: browserAgentTools,
      regions: [
        {
          id: 'browser.network',
          label: 'Network',
          icon: '📡',
          position: 'right',
          defaultSize: 360,
        },
      ],
    },
  ],
  widgets: [
    {
      id: 'browser.network',
      title: 'Browser network',
      component: NetworkStrip,
      // A region strip of `browser.view` (the 📡 toggle). Embedded, because it
      // reports on *a browser session* and has nothing to show without one — but
      // still a real registered view, so it can be dragged out to its own area,
      // where the full request inspector actually has room.
      role: 'widget',
      icon: '📡',
      embedded: true,
    },
  ],
  commands: [
    {
      id: 'browser.open',
      title: 'Browser: New tab',
      run: () => registry.openPanel('browser.view'),
    },
    {
      id: 'browser.focusUrlBar',
      title: 'Browser: Focus URL bar',
      run: () => focusActiveUrlBar(),
    },
  ],
  keybindings: [
    // Scoped to a focused browser pane so it never shadows a global mod+l.
    { key: 'mod+l', command: 'browser.focusUrlBar', scope: 'browser.view' },
  ],
  settings: [
    {
      key: 'browser.homePage',
      title: 'Home page',
      description: 'URL opened by the Home button (blank shows a start page).',
      type: 'string',
      default: '',
    },
    {
      key: 'browser.readerModeDefault',
      title: 'Open pages in reader mode',
      description: 'Fetch the readable extracted version of every page by default.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'browser.saveLibrary',
      title: 'Save to library',
      description: 'Which knowledge library the browser’s Save button files pages and media into.',
      type: 'string',
      default: 'default',
    },
    {
      key: 'browser.engine',
      title: 'Rendering engine',
      description:
        'full = real headless Chromium server-rendered from the backend (reads the live DOM, persists cookies/cache, agent can scrape/act); iframe = the light embedded frame. auto uses full when the backend has it enabled (HORRIBLE_ENABLE_SERVER_BROWSER=1) and falls back to iframe.',
      type: 'enum',
      enumValues: ['auto', 'full', 'iframe'],
      default: 'auto',
    },
  ],
};
