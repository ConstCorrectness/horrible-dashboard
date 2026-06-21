import { registry, type ModuleManifest } from '../../registry';
import { VectorDbWidget } from './widgets';

export const vectordbModule: ModuleManifest = {
  id: 'vectordb',
  title: 'Vector DB',
  widgets: [
    {
      id: 'vectordb.manager',
      title: 'Vector Database',
      component: VectorDbWidget,
      defaultPlacement: 'center',
      agentTools: [
        {
          name: 'vectordb.search',
          description: 'Search the vector database semantically. Returns list of documents matching the query.',
          params: {
            type: 'object',
            properties: {
              collection: { type: 'string', description: 'Name of the collection to search' },
              text: { type: 'string', description: 'Query text' },
              limit: { type: 'integer', description: 'Max number of documents to return (default 5)' }
            },
            required: ['collection', 'text']
          },
          handler: async (args) => {
            const { collection, text, limit } = args as { collection: string; text: string; limit?: number };
            const { apiPost } = await import('../../api');
            return apiPost('/vectordb/search', { collection, text, limit: limit ?? 5 });
          }
        },
        {
          name: 'vectordb.upsert',
          description: 'Insert or update a document in the vector database.',
          params: {
            type: 'object',
            properties: {
              collection: { type: 'string', description: 'Collection name' },
              text: { type: 'string', description: 'The text content to store' },
              id: { type: 'string', description: 'Optional ID. If omitted, a random ID will be generated.' },
              metadata: { type: 'object', description: 'Optional dictionary of key-value metadata pairs.' }
            },
            required: ['collection', 'text']
          },
          sideEffect: true,
          handler: async (args) => {
            const { collection, text, id, metadata } = args as {
              collection: string;
              text: string;
              id?: string;
              metadata?: Record<string, unknown>;
            };
            const { apiPost } = await import('../../api');
            return apiPost('/vectordb/documents', { collection, text, id, metadata: metadata ?? {} });
          }
        },
        {
          name: 'vectordb.delete',
          description: 'Delete a document from the vector database by its ID.',
          params: {
            type: 'object',
            properties: {
              id: { type: 'string', description: 'The document ID to delete' }
            },
            required: ['id']
          },
          sideEffect: true,
          handler: async (args) => {
            const { id } = args as { id: string };
            const { apiDelete } = await import('../../api');
            return apiDelete(`/vectordb/documents/${id}`);
          }
        },
        {
          name: 'vectordb.get_stats',
          description: 'Retrieve statistics and status of the vector database.',
          params: { type: 'object', properties: {} },
          handler: async () => {
            const { apiGet } = await import('../../api');
            return apiGet('/vectordb/status');
          }
        }
      ]
    }
  ],
  commands: [
    {
      id: 'vectordb.open',
      title: 'Vector DB: Open Manager',
      run: () => registry.openPanel('vectordb.manager'),
    }
  ]
};
