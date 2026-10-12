/**
 * Knowledge base module: a catalog of saved learning material — bookmarks,
 * articles, imported primers and courses — with reading status, notes, course
 * progress, offline archives, and retrieval the agent cites. See docs/modules/kb.mdx.
 *
 * Retrieval and curation tools are backend tools (`backend/modules/kb/agent_tools.py`)
 * so they work with no tab attached; the one tool declared here is `kb.open`, which
 * puts an item in front of the user and so only means something with a UI.
 */
import { lazyPane } from '../../lazy-pane';
import { registry, type ModuleManifest } from '../../registry';
import { getItem } from './api';
import { openItem } from './open';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const KnowledgePane = lazyPane(() => import('./panels/KnowledgePane'), 'KnowledgePane');

export const kbModule: ModuleManifest = {
  id: 'kb',
  title: 'Knowledge',
  category: 'data',
  settings: [
    {
      key: 'kb.chunkSize',
      title: 'Knowledge-base chunk size (characters)',
      description:
        'Target size of each indexed passage for saved pages and imported sites. Chunks follow the ' +
        "page's headings, so a passage is cited by its section. Independent of the Library's chunk " +
        'size. Applies to pages indexed after the change.',
      type: 'number',
      default: 1200,
    },
  ],
  widgets: [
    {
      id: 'kb.browse',
      title: 'Knowledge',
      component: KnowledgePane,
      role: 'document',
      // Rail glyph: the one place an emoji is the convention (see CLAUDE.local.md).
      icon: '🎓',
      agentTools: [
        {
          name: 'kb.open',
          description:
            'Put a knowledge-base item in front of the user: the live page in the browser, or its ' +
            "offline archive ('offline') when it has one. Use the itemId from kb.search or kb.list.",
          params: {
            type: 'object',
            properties: {
              itemId: { type: 'string', description: 'The item to open.' },
              where: {
                type: 'string',
                enum: ['live', 'offline'],
                description: "Default 'offline' when an archive exists, else 'live'.",
              },
            },
            required: ['itemId'],
          },
          handler: async (args) => {
            const { itemId, where } = args as { itemId: string; where?: 'live' | 'offline' };
            const item = await getItem(itemId);
            const mode = where ?? (item.docpage_id || item.artifact_id ? 'offline' : 'live');
            const opened = openItem(item, mode);
            return opened
              ? { opened: item.title, where: mode }
              : { error: 'This item has no URL or offline copy to open.' };
          },
        },
      ],
    },
  ],
  commands: [
    {
      id: 'kb.open',
      title: 'Knowledge: Open',
      run: () => registry.openPanel('kb.browse'),
    },
    {
      id: 'kb.importSite',
      title: 'Knowledge: Import a site or course…',
      run: () => registry.openPanel('kb.browse', { params: { import: '' } }),
    },
  ],
  keybindings: [{ key: 'mod+alt+k', command: 'kb.open' }],
};
