/**
 * MCP module: the summary renderer shared by `/mcp`, `mcp.status` and the pane.
 *
 * `summarize` is tested rather than the manifest because importing the manifest pulls
 * in the React pane and the registry, which reach WS-at-module-scope code that has no
 * jsdom under vitest. The manifest's shape is covered by `pnpm typecheck`; what needs
 * asserting here is the logic the three surfaces share, since drift between them is the
 * exact bug the shared client exists to prevent.
 */
import { describe, expect, it } from 'vitest';

import { summarize, type McpServer } from '../api';

function server(overrides: Partial<McpServer>): McpServer {
  return {
    id: 's',
    name: '',
    transport: 'stdio',
    command: 'npx',
    args: [],
    env: {},
    cwd: null,
    url: '',
    enabled: true,
    group: 'mcp-s',
    state: 'stopped',
    error: null,
    serverName: '',
    serverVersion: '',
    protocolVersion: '',
    origin: 'manual',
    project: '',
    hasToken: false,
    target: { available: true },
    tools: [],
    prompts: [],
    resources: [],
    secretEnv: [],
    missingSecretEnv: [],
    ...overrides,
  };
}

describe('summarize', () => {
  it('tells the user how to add one when nothing is configured', () => {
    const text = summarize([]);
    expect(text).toContain('No MCP servers configured');
    expect(text).toContain('MCP: Add server');
  });

  it('reports the tool count and group for a ready server', () => {
    const text = summarize([
      server({
        id: 'fs',
        state: 'ready',
        group: 'mcp-fs',
        tools: [
          { name: 'read', description: '', readOnly: true, destructive: false, inputSchema: {} },
          { name: 'write', description: '', readOnly: false, destructive: false, inputSchema: {} },
        ],
      }),
    ]);
    expect(text).toContain('fs');
    expect(text).toContain('2 tools');
    expect(text).toContain('mcp-fs');
  });

  it('singularizes a one-tool server', () => {
    const text = summarize([
      server({
        state: 'ready',
        tools: [
          { name: 'only', description: '', readOnly: false, destructive: false, inputSchema: {} },
        ],
      }),
    ]);
    expect(text).toContain('1 tool ');
    expect(text).not.toContain('1 tools');
  });

  it('surfaces the reason a server failed, not just that it did', () => {
    const text = summarize([
      server({ id: 'broken', state: 'error', error: 'command not on PATH' }),
    ]);
    expect(text).toContain('broken');
    expect(text).toContain('command not on PATH');
  });

  it('falls back to a message when an errored server has no detail', () => {
    const text = summarize([server({ state: 'error', error: null })]);
    expect(text).toContain('unknown error');
  });

  it('lists every server, one line each, under a count header', () => {
    const text = summarize([
      server({ id: 'a', state: 'ready' }),
      server({ id: 'b', state: 'stopped' }),
      server({ id: 'c', state: 'starting' }),
    ]);
    const lines = text.split('\n');
    expect(lines[0]).toContain('(3)');
    expect(lines).toHaveLength(4);
  });
});
