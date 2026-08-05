/**
 * Documentation popup module: the settings and the shared renderer behind the
 * hover/Shift-Tab docs in the editor and in notebook cells.
 *
 * It contributes no pane of its own — the popup belongs to whatever is being
 * hovered — but it is a module rather than loose helpers because the source chain
 * is user-configurable, and a setting has to be declared by someone. See
 * docs/modules/docs-popup.mdx.
 */
import type { ModuleManifest } from '../../registry';
import { DEFAULT_DOC_SOURCES } from '../../docs/chain';

export const docsModule: ModuleManifest = {
  id: 'docs',
  title: 'Documentation',
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
