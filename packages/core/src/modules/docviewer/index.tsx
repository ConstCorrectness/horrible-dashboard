/**
 * Documentation viewer module: whole documentation sites captured once and read
 * offline at full fidelity — real CSS, real JavaScript, a page tree and semantic
 * search over the set. See docs/modules/docviewer.mdx.
 *
 * Distinct from the `docs` module, which despite the name is the symbol-hover popup
 * in the editor and contributes no pane.
 */
import { revealSection } from '../../layout/controller';
import { lazyPane } from '../../lazy-pane';
import type { ModuleManifest } from '../../registry';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const DocSetBrowser = lazyPane(() => import('./panels/DocSetBrowser'), 'DocSetBrowser');

export const docviewerModule: ModuleManifest = {
  id: 'docviewer',
  title: 'Doc viewer',
  category: 'data',
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
      //
      // One Docs window, two kinds of documentation. The Python reference (the
      // `docs` module's view, `embedded`) was a second launcher entry for the
      // same verb — "read the docs" — so it is a section here. It is the default
      // because it works on a fresh install; a doc set has to be captured first.
      sections: [
        { id: 'python', label: 'Python reference', icon: 'λ', view: 'docs.reference', default: true },
        { id: 'sets', label: 'Doc sets', icon: '▤' },
      ],
    },
  ],
  commands: [
    {
      id: 'docviewer.open',
      title: 'Docs: Open doc viewer',
      run: () => void revealSection('sets', 'docviewer.browse'),
    },
  ],
  keybindings: [{ key: 'mod+shift+d', command: 'docviewer.open' }],
};
