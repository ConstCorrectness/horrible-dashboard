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
            'List the database connections the user can query. Returns each connection id, name, provider, and dialect. Two built-ins are always present: "app" is the dashboard\'s own SQLite database (SQL), and "vectors" is its LanceDB vector store (JSON dialect). A third, "atlas", is present only on a node that administers the shared MongoDB Atlas cluster (mongo dialect). Check "dialect" before writing a query — "sql" takes SQL, "json" takes a vector-store operation body, "mongo" takes a MongoDB operation body.',
          params: { type: 'object', properties: {} },
          handler: async () => {
            const { connections } = await listConnections();
            return connections.map((c) => ({
              id: c.id,
              name: c.name,
              provider: c.provider,
              dialect: c.dialect,
              builtin: c.builtin,
            }));
          },
        },
        {
          name: 'database.describe',
          description:
            'Describe a connection\'s schema so you can write a correct query. For SQL connections these are tables and their columns/types; for vector stores and MongoDB they are collections and their fields (MongoDB field types are sampled from documents, since a collection has no declared schema). Also returns the connection\'s dialect. Defaults to the built-in "app" connection.',
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
            const id = connection_id ?? 'app';
            // The dialect decides whether the agent should write SQL or a JSON body,
            // so it travels with the schema rather than needing a second call.
            const { connections } = await listConnections();
            return {
              dialect: connections.find((c) => c.id === id)?.dialect ?? 'sql',
              schema: await describe(id),
            };
          },
        },
        {
          name: 'database.query',
          description:
            'Run a read-only query against a connection and return columns and rows. For "sql"-dialect connections pass a single SELECT/WITH/EXPLAIN statement. For "json"-dialect (vector store) connections pass a JSON operation body instead, e.g. {"op":"search","collection":"library","query":"some text","limit":5} — read ops are search, get, list, count, peek, collections, describe. For "mongo"-dialect (MongoDB/Atlas) connections pass a MongoDB operation body, e.g. {"op":"find","collection":"presence","filter":{},"limit":20} — read ops are find, aggregate, count, distinct, collections, databases, describe, indexes, stats; bodies are Extended JSON, so an ObjectId is written {"$oid":"…"}. Call database.listConnections or database.describe first if you do not know the dialect. Defaults to the built-in "app" connection.',
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
            'Execute a writing statement against a connection: SQL (INSERT/UPDATE/DELETE/DDL) for "sql" connections, or a write operation body for "json"/"mongo" ones (e.g. {"op":"update","collection":"x","filter":{…},"update":{"$set":{…}}}). Use only when the user wants to modify data. Some connections refuse writes regardless — the shared "atlas" cluster is read-only unless the node is configured otherwise. Defaults to the built-in "app" connection.',
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
