/**
 * Knowledge library module: ingest blogs and notes into the app's vector store and
 * search them semantically (the retrieval half of RAG — the agent generates answers
 * from the cited chunks these tools return). See docs/modules/library.mdx.
 */
import { registry, type ModuleManifest } from '../../registry';
import {
  ingestSource,
  librarySearch,
  listSources,
  type IngestRequest,
  type SourceType,
} from './api';
import { LibraryPanel } from './LibraryPanel';
import { getCurrentLibrary } from './store';

export const libraryModule: ModuleManifest = {
  id: 'library',
  title: 'Library',
  settings: [
    {
      key: 'library.defaultLibrary',
      title: 'Default library',
      description: 'The library (collection) the panel opens on.',
      type: 'string',
      default: 'default',
    },
    {
      key: 'library.chunkSize',
      title: 'Chunk size (characters)',
      description: 'Target size of each embedded text chunk during ingestion.',
      type: 'number',
      default: 1000,
    },
  ],
  widgets: [
    {
      id: 'library.panel',
      title: 'Library',
      component: LibraryPanel,
      role: 'document',
      icon: '📚',
      agentTools: [
        {
          name: 'library.search',
          description:
            'Semantic search over a knowledge library (blogs and notes the user has ingested). Returns matches grouped by source with citation info (title, url) and the matching text chunks — use these to answer the question and cite sources. Defaults to the current library.',
          params: {
            type: 'object',
            properties: {
              text: { type: 'string', description: 'The query text.' },
              library: {
                type: 'string',
                description: 'Library name (defaults to the current one).',
              },
              limit: {
                type: 'integer',
                description: 'Max chunks to retrieve (default 8).',
              },
            },
            required: ['text'],
          },
          handler: async (args) => {
            const { text, library, limit } = args as {
              text: string;
              library?: string;
              limit?: number;
            };
            return librarySearch(library ?? getCurrentLibrary(), text, limit ?? 8);
          },
        },
        {
          name: 'library.listSources',
          description:
            'List the sources (blogs/notes) in a library, with their ingestion status and chunk counts. Defaults to the current library.',
          params: {
            type: 'object',
            properties: {
              library: {
                type: 'string',
                description: 'Library name (defaults to the current one).',
              },
            },
          },
          handler: async (args) => {
            const { library } = args as { library?: string };
            return listSources(library ?? getCurrentLibrary());
          },
        },
        {
          name: 'library.addSource',
          description:
            'Add a source to a library and start ingesting it. Use type "blog" with a url to clip a web page, or type "note" with text to save notes. Ingestion (fetch, chunk, embed) runs in the background.',
          params: {
            type: 'object',
            properties: {
              type: {
                type: 'string',
                description: '"blog" (needs url) or "note" (needs text).',
              },
              url: { type: 'string', description: 'Blog/article URL, for type "blog".' },
              title: { type: 'string', description: 'Title (optional for blog).' },
              text: { type: 'string', description: 'Note body, for type "note".' },
              tags: {
                type: 'array',
                items: { type: 'string' },
                description: 'Optional tags.',
              },
              library: {
                type: 'string',
                description: 'Library name (defaults to the current one).',
              },
            },
            required: ['type'],
          },
          sideEffect: true,
          handler: async (args) => {
            const a = args as {
              type: SourceType;
              url?: string;
              title?: string;
              text?: string;
              tags?: string[];
              library?: string;
            };
            const req: IngestRequest = {
              type: a.type,
              library: a.library ?? getCurrentLibrary(),
              url: a.url,
              title: a.title,
              text: a.text,
              tags: a.tags,
            };
            return ingestSource(req);
          },
        },
      ],
    },
  ],
  commands: [
    {
      id: 'library.open',
      title: 'Library: Open',
      run: () => registry.openPanel('library.panel'),
    },
  ],
};
