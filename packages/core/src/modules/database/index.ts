import { registry, type ModuleManifest } from '../../registry';
import { DatabaseConsole } from './widgets';
import { getSchema, listConnections, runQuery, semanticSearch, type SchemaResponse } from './api';

/** Compact schema for the agent: "table(col type, col type PK, …)" lines. */
async function describe(connectionId: string): Promise<string> {
  const schema: SchemaResponse = await getSchema(connectionId);
  if (schema.tables.length === 0) return '(no tables)';
  return schema.tables
    .map((t) => {
      const name = t.schema_name ? `${t.schema_name}.${t.name}` : t.name;
      const cols = t.columns
        .map((c) => `${c.name} ${c.type}${c.primary_key ? ' PK' : ''}`)
        .join(', ');
      return `${name}(${cols})`;
    })
    .join('\n');
}

export const databaseModule: ModuleManifest = {
  id: 'database',
  title: 'Database',
  settings: [
    {
      key: 'database.defaultConnection',
      title: 'Default connection',
      description: 'Connection id selected when the console opens (e.g. "app").',
      type: 'string',
      default: 'app',
    },
    {
      key: 'database.rowLimit',
      title: 'Result row limit',
      description: 'Maximum rows fetched per query before results are truncated.',
      type: 'number',
      default: 1000,
    },
  ],
  widgets: [
    {
      id: 'database.console',
      title: 'Database Console',
      component: DatabaseConsole,
      role: 'document',
      editor: true,
      icon: '🛢',
      agentTools: [
        {
          name: 'database.listConnections',
          description:
            'List the database connections the user can query. Returns each connection id, name, and provider. The built-in "app" connection is the dashboard\'s own local vector store.',
          params: { type: 'object', properties: {} },
          handler: async () => {
            const { connections } = await listConnections();
            return connections.map((c) => ({
              id: c.id,
              name: c.name,
              provider: c.provider,
              builtin: c.builtin,
            }));
          },
        },
        {
          name: 'database.describe',
          description:
            'Describe a connection\'s schema (tables and their columns/types) so you can write correct SQL. Defaults to the built-in "app" connection.',
          params: {
            type: 'object',
            properties: {
              connection_id: {
                type: 'string',
                description: 'Connection id (default "app").',
              },
            },
          },
          handler: async (args) => {
            const { connection_id } = args as { connection_id?: string };
            return { schema: await describe(connection_id ?? 'app') };
          },
        },
        {
          name: 'database.query',
          description:
            'Run a read-only SQL query (a single SELECT/WITH/EXPLAIN statement) against a connection and return columns and rows. Defaults to the built-in "app" connection.',
          params: {
            type: 'object',
            properties: {
              sql: { type: 'string', description: 'The SELECT statement to run.' },
              connection_id: {
                type: 'string',
                description: 'Connection id (default "app").',
              },
            },
            required: ['sql'],
          },
          handler: async (args) => {
            const { sql, connection_id } = args as {
              sql: string;
              connection_id?: string;
            };
            return runQuery({
              connection_id: connection_id ?? 'app',
              sql,
              read_only: true,
            });
          },
        },
        {
          name: 'database.execute',
          description:
            'Execute a writing SQL statement (INSERT/UPDATE/DELETE/DDL) against a connection. Use only when the user wants to modify data. Defaults to the built-in "app" connection.',
          params: {
            type: 'object',
            properties: {
              sql: { type: 'string', description: 'The statement to execute.' },
              connection_id: {
                type: 'string',
                description: 'Connection id (default "app").',
              },
            },
            required: ['sql'],
          },
          sideEffect: true,
          specifierTemplate: '{connection_id}',
          handler: async (args) => {
            const { sql, connection_id } = args as {
              sql: string;
              connection_id?: string;
            };
            return runQuery({
              connection_id: connection_id ?? 'app',
              sql,
              read_only: false,
            });
          },
        },
        {
          name: 'database.semanticSearch',
          description:
            'Semantic (vector) search over a collection in the built-in app vector store. Returns documents ranked by cosine similarity.',
          params: {
            type: 'object',
            properties: {
              collection: { type: 'string', description: 'Collection name to search.' },
              text: { type: 'string', description: 'Query text.' },
              limit: {
                type: 'integer',
                description: 'Max documents to return (default 5).',
              },
            },
            required: ['collection', 'text'],
          },
          handler: async (args) => {
            const { collection, text, limit } = args as {
              collection: string;
              text: string;
              limit?: number;
            };
            return semanticSearch(collection, text, limit ?? 5);
          },
        },
      ],
    },
  ],
  commands: [
    {
      id: 'database.open',
      title: 'Database: Open Console',
      run: () => registry.openPanel('database.console'),
    },
  ],
};
