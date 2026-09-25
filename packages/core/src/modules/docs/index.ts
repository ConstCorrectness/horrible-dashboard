/**
 * Documentation popup module: the settings and the shared renderer behind the
 * hover/Shift-Tab docs in the editor and in notebook cells.
 *
 * The popup belongs to whatever is being hovered, so it is not a pane. The one
 * pane is the **Python reference** — the stdlib, every installed package and the
 * dashboard's own SDKs, browsable at the installed versions — because hovering is
 * how you read about a symbol you already have, never how you find one you don't.
 * See docs/modules/docs-popup.mdx.
 */
import { lazyPane } from '../../lazy-pane';
import { registry, type ModuleManifest } from '../../registry';
import { DEFAULT_DOC_SOURCES } from '../../docs/chain';
import { sendReferenceQuery } from './reference-api';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const ReferencePane = lazyPane(() => import('./ReferencePane'), 'ReferencePane');

export const docsModule: ModuleManifest = {
  id: 'docs',
  title: 'Documentation',
  category: 'data',
  panels: [
    {
      id: 'docs.reference',
      title: 'Python reference',
      component: ReferencePane,
      // Read and worked in, and tabbed beside notebooks in the AI Research preset.
      role: 'document',
      icon: '📚',
      singleton: true,
      // The Python reference is the default section of the Docs window
      // (`docviewer.browse`), not a window of its own; `openPanel` on this id
      // reveals it there.
      embedded: true,
    },
  ],
  commands: [
    {
      id: 'docs.openReference',
      title: 'Python reference: Browse the stdlib, packages and SDKs',
      run: () => registry.openPanel('docs.reference'),
      slash: 'pydoc',
    },
    {
      id: 'docs.searchReference',
      title: 'Python reference: Search for the symbol under the cursor',
      run: () => {
        const selection = window.getSelection()?.toString().trim();
        if (selection) sendReferenceQuery(selection);
        registry.openPanel('docs.reference');
      },
    },
  ],
  settings: [
    {
      key: 'docs.sources',
      title: 'Documentation sources',
      description:
        'Comma-separated, in priority order. The first source with an answer wins. ' +
        'kernel = the live notebook namespace; lsp = the language server; ' +
        'index = the offline package/stdlib index; web = a guarded web search. ' +
        'Remove a name to disable it.',
      // One ordered string rather than four toggles: enabling a source and ranking
      // it are the same decision, and two settings that must agree is a way to end
      // up with `web` enabled but never reached.
      type: 'string',
      default: DEFAULT_DOC_SOURCES,
    },
    {
      key: 'docs.hover',
      title: 'Show documentation on hover',
      description:
        'Hovering a symbol opens its documentation. Turn off to keep the popup on ' +
        'the explicit shortcut only (Shift+Tab in a notebook cell).',
      type: 'boolean',
      default: true,
    },
    {
      key: 'docs.webOnHover',
      title: 'Allow web lookups on hover',
      description:
        'Off by default. The web source is the only one that leaves your machine: ' +
        'a lookup takes several seconds and spends a search-API call on whatever ' +
        'symbol the pointer happens to rest on. Shift+Tab uses it either way.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'docs.hoverDelayMs',
      title: 'Hover delay (ms)',
      description: 'How long the pointer must rest on a symbol before docs appear.',
      type: 'number',
      default: 400,
      advanced: true,
    },
  ],
};
