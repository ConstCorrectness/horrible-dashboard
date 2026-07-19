import { describe, expect, it } from 'vitest';
import type { SchemaResponse } from '../api';
import { schemaToSqlConfig } from '../sqlSchema';

const col = (name: string, primary = false) => ({
  name,
  type: 'TEXT',
  nullable: !primary,
  primary_key: primary,
});

describe('schemaToSqlConfig', () => {
  it('returns an empty schema for null/empty input', () => {
    expect(schemaToSqlConfig(null)).toEqual({ schema: {} });
    expect(schemaToSqlConfig({ tables: [] })).toEqual({ schema: {} });
  });

  it('maps unqualified tables to column-name lists', () => {
    const schema: SchemaResponse = {
      tables: [{ name: 'users', schema_name: null, columns: [col('id', true), col('email')] }],
    };
    expect(schemaToSqlConfig(schema)).toEqual({ schema: { users: ['id', 'email'] } });
  });

  it('qualifies tables with their schema and picks the dominant defaultSchema', () => {
    const schema: SchemaResponse = {
      tables: [
        { name: 'users', schema_name: 'public', columns: [col('id', true)] },
        { name: 'orders', schema_name: 'public', columns: [col('id', true), col('user_id')] },
        { name: 'jobs', schema_name: 'internal', columns: [col('id', true)] },
      ],
    };
    expect(schemaToSqlConfig(schema)).toEqual({
      schema: {
        'public.users': ['id'],
        'public.orders': ['id', 'user_id'],
        'internal.jobs': ['id'],
      },
      defaultSchema: 'public',
    });
  });
});
