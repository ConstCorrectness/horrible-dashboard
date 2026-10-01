import { lazyPane } from '../../lazy-pane';
import { registry, type ModuleManifest } from '../../registry';
import { browserAgentTools } from './agentTools';
import { focusActiveUrlBar, openActiveDevtools } from './panels/BrowserPanel';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const BrowserPanel = lazyPane(() => import('./panels/BrowserPanel'), 'BrowserPanel');
const NetworkStrip = lazyPane(() => import('./panels/NetworkStrip'), 'NetworkStrip');

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
  category: 'data',
  panels: [
    {
      id: 'browser.view',
      title: 'Browser',
      component: BrowserPanel,
      role: 'document',
      // A web page is a full-page medium; reading one inside a window inside a
      // desktop inside a window is two frames too many.
      fullscreen: true,
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
    {
      // Native pane only (the page's own F12 works too once the page has focus).
      id: 'browser.devtools',
      title: 'Browser: Developer tools',
      run: () => openActiveDevtools(),
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
        'native = a real browser (WebView2) inside the pane, with tabs, downloads, devtools and saved logins (desktop only; on Windows the agent drives the same page you see, elsewhere it cannot read it); full = headless Chromium in the backend streamed to the pane (HORRIBLE_ENABLE_SERVER_BROWSER=1); iframe = the light embedded frame. auto prefers native on Windows, otherwise full when the backend has it enabled, then native, then iframe.',
      type: 'enum',
      enumValues: ['auto', 'full', 'native', 'iframe'],
      default: 'auto',
    },
  ],
};
