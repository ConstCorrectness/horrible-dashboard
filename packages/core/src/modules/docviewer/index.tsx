/**
 * Documentation viewer module: whole documentation sites captured once and read
 * offline at full fidelity — real CSS, real JavaScript, a page tree and semantic
 * search over the set. See docs/modules/docviewer.mdx.
 *
 * Distinct from the `docs` module, which despite the name is the symbol-hover popup
 * in the editor and contributes no pane.
 */
import { registry, type ModuleManifest } from '../../registry';
import { DocSetBrowser } from './panels/DocSetBrowser';

export const docviewerModule: ModuleManifest = {
  id: 'docviewer',
  title: 'Doc viewer',
  settings: [
    {
      key: 'docviewer.crawlDelay',
      title: 'Delay between pages (seconds)',
      description:
        'How long to wait between requests to the same site while capturing a doc set. ' +
        "A site's own robots.txt Crawl-delay can raise this but never lower it.",
      type: 'number',
      default: 1,
    },
    {
      key: 'docviewer.defaultMaxPages',
      title: 'Default page limit',
      description:
        'How many pages a new doc set captures before stopping. Each page is stored ' +
        'with its stylesheets and images inlined, so a large set is a large folder.',
      type: 'number',
      default: 200,
    },
  ],
  widgets: [
    {
      id: 'docviewer.browse',
      title: 'Docs',
      component: DocSetBrowser,
      role: 'document',
      // Rail glyph: the one place an emoji is the convention (see CLAUDE.local.md).
      icon: '📖',
      // Non-singleton: reading two sets side by side is a normal thing to want.
      // Openers pass `{ setId?, pageId? }`.
    },
  ],
  commands: [
    {
      id: 'docviewer.open',
      title: 'Docs: Open doc viewer',
      run: () => registry.openPanel('docviewer.browse'),
    },
  ],
  keybindings: [{ key: 'mod+shift+d', command: 'docviewer.open' }],
};
