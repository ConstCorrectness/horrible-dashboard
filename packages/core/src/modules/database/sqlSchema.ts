/**
 * Pure mapping from the backend's introspected `SchemaResponse` to the config
 * shape `@codemirror/lang-sql` wants for schema-aware completion. Kept free of
 * CodeMirror/React imports so it is unit-testable without a DOM.
 */
import type { SchemaResponse } from './api';

/** Table → column names, with `schema.table` keys when a schema is set
 * (lang-sql resolves dotted keys into nested namespaces). */
export interface SqlSchemaConfig {
  schema: Record<string, string[]>;
  defaultSchema?: string;
}

export function schemaToSqlConfig(schema: SchemaResponse | null): SqlSchemaConfig {
  const out: Record<string, string[]> = {};
  const schemaNames = new Map<string, number>();
  for (const table of schema?.tables ?? []) {
    const qualified = table.schema_name ? `${table.schema_name}.${table.name}` : table.name;
    out[qualified] = table.columns.map((c) => c.name);
    if (table.schema_name) {
      schemaNames.set(table.schema_name, (schemaNames.get(table.schema_name) ?? 0) + 1);
    }
  }
  // Let unqualified names complete against the dominant schema (postgres `public`
  // in the common case) — pick the schema holding the most tables.
  let defaultSchema: string | undefined;
  let best = 0;
  for (const [name, count] of schemaNames) {
    if (count > best) {
      best = count;
      defaultSchema = name;
    }
  }
  return defaultSchema ? { schema: out, defaultSchema } : { schema: out };
}
