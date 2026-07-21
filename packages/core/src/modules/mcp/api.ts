/**
 * MCP module API client — the single source of truth every surface reads.
 *
 * The servers pane, the `mcp.*` palette commands and the chat's `/mcp` slash command
 * all go through here rather than each calling fetch. That is what keeps `/mcp` from
 * being a second, drifting implementation of "list my MCP servers": it renders the
 * same data the pane does, just as ephemeral text.
 *
 * A token is write-only across this boundary — it can be sent in `saveServer`, and is
 * only ever read back as the `hasToken` boolean.
 */
import { apiDelete, apiGet, apiPost } from '../../api';

export type McpTransport = 'stdio' | 'http' | 'sse';
export type McpState = 'stopped' | 'starting' | 'ready' | 'error';

export interface McpTool {
  name: string;
  description: string;
  readOnly: boolean;
  destructive: boolean;
}

export interface McpPrompt {
  name: string;
  description: string;
}

export interface McpResource {
  uri: string;
  name: string;
  description: string;
}

export interface McpServer {
  id: string;
  name: string;
  transport: McpTransport;
  command: string;
  args: string[];
  env: Record<string, string>;
  cwd: string | null;
  url: string;
  enabled: boolean;
  /** The agent tool group this server's tools are disclosed under (`mcp-<id>`). */
  group: string;
  state: McpState;
  error: string | null;
  serverName: string;
  serverVersion: string;
  hasToken: boolean;
  /** What this node would actually run, and whether the command resolves on PATH. */
  target: {
    command?: string;
    resolved?: string | null;
    available: boolean;
    argv?: string[];
    url?: string;
  };
  tools: McpTool[];
  prompts: McpPrompt[];
  resources: McpResource[];
}

export interface McpServerInput {
  id: string;
  name?: string;
  transport: McpTransport;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  cwd?: string | null;
  url?: string;
  headers?: Record<string, string>;
  /** Write-only: stored server-side encrypted, never returned. */
  token?: string;
  enabled?: boolean;
}

export function listServers(): Promise<{ servers: McpServer[] }> {
  return apiGet<{ servers: McpServer[] }>('/mcp/servers');
}

export function saveServer(input: McpServerInput): Promise<McpServer> {
  return apiPost<McpServer>('/mcp/servers', input);
}

export function deleteServer(id: string): Promise<{ servers: McpServer[] }> {
  return apiDelete<{ servers: McpServer[] }>(`/mcp/servers/${encodeURIComponent(id)}`);
}

export function connectServer(id: string): Promise<McpServer> {
  return apiPost<McpServer>(`/mcp/servers/${encodeURIComponent(id)}/connect`, {});
}

export function disconnectServer(id: string): Promise<McpServer> {
  return apiPost<McpServer>(`/mcp/servers/${encodeURIComponent(id)}/disconnect`, {});
}

export function readResource(
  id: string,
  uri: string,
): Promise<{ contents: unknown[]; error: string | null }> {
  return apiPost(`/mcp/servers/${encodeURIComponent(id)}/resource`, { uri });
}

/**
 * A one-line-per-server summary, shared by `/mcp` in chat and the `mcp.status`
 * command. Lives here so the two can never disagree about what "connected" means.
 */
export function summarize(servers: McpServer[]): string {
  if (servers.length === 0) {
    return 'No MCP servers configured. Add one with the "MCP: Add server" command.';
  }
  const icon: Record<McpState, string> = {
    ready: '●',
    starting: '◐',
    error: '✕',
    stopped: '○',
  };
  const lines = servers.map((s) => {
    const head = `${icon[s.state]} ${s.id} — ${s.state}`;
    if (s.state === 'ready') {
      const tools = `${s.tools.length} tool${s.tools.length === 1 ? '' : 's'}`;
      return `${head} · ${tools} · group \`${s.group}\``;
    }
    if (s.state === 'error') return `${head} — ${s.error ?? 'unknown error'}`;
    return head;
  });
  return [`MCP servers (${servers.length}):`, ...lines].join('\n');
}
