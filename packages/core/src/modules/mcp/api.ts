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

/** Where a server's code came from. Shown before anything is run. */
export type McpOrigin = 'manual' | 'registry' | 'authored';

/** A JSON Schema, as far as the invoke form needs to understand one. */
export interface McpSchema {
  type?: string;
  properties?: Record<string, McpSchema>;
  required?: string[];
  description?: string;
  default?: unknown;
  enum?: unknown[];
  items?: McpSchema;
}

export interface McpTool {
  name: string;
  description: string;
  readOnly: boolean;
  destructive: boolean;
  /** The server's own argument schema — what the invoke form is generated from. */
  inputSchema: McpSchema;
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
  protocolVersion: string;
  /** Whose code this is: a registry third party's, the user's own project, or typed. */
  origin: McpOrigin;
  /** The authoring project owning this server's files, when `origin` is `authored`. */
  project: string;
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
  /** Names of environment variables whose values are held encrypted server-side. */
  secretEnv: string[];
  /** Declared secrets with nothing stored yet — "configured but needs a key". */
  missingSecretEnv: string[];
}

/** One JSON-RPC message as it crossed the wire. */
export interface McpWireMessage {
  at: number;
  direction: 'in' | 'out';
  method: string;
  id: string;
  payload: string;
  truncated: boolean;
}

export interface McpCost {
  tools: Array<{ name: string; tokens: number }>;
  toolTokens: number;
  guideTokens: number;
  totalTokens: number;
  /** False means chars/4 estimates — the pane must say so rather than imply precision. */
  exact: boolean;
  tokenizer: string;
  agents: Array<{ id: string; name: string; explicit: boolean }>;
}

export interface McpEnvVar {
  name: string;
  description: string;
  required: boolean;
  secret: boolean;
  default: string;
}

export interface McpInstallOption {
  kind: 'package' | 'remote';
  label: string;
  transport: McpTransport;
  command: string;
  args: string[];
  url: string;
  env: McpEnvVar[];
  /** Why this option can't be used as-is. Empty when it can. */
  unsupported: string;
}

export interface McpCatalogEntry {
  name: string;
  title: string;
  description: string;
  version: string;
  repository: string;
  source: 'registry' | 'curated';
  note: string;
  suggestedId: string;
  installs: McpInstallOption[];
}

export interface McpProbe {
  ok: boolean;
  error: string | null;
  serverName: string;
  serverVersion: string;
  instructions: string;
  tools: McpTool[];
  prompts: McpPrompt[];
  resources: McpResource[];
  messages: McpWireMessage[];
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
  /** Names of env vars whose values are secret. Kept in the config; values are not. */
  secretEnv?: string[];
  /** Provenance, set by whichever surface adds the server. */
  origin?: McpOrigin;
  /** Write-only: values for those names, routed to the encrypted store. */
  secretEnvValues?: Record<string, string>;
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

export function serverTranscript(id: string): Promise<{ messages: McpWireMessage[] }> {
  return apiGet<{ messages: McpWireMessage[] }>(
    `/mcp/servers/${encodeURIComponent(id)}/transcript`,
  );
}

export function clearTranscript(id: string): Promise<{ messages: McpWireMessage[] }> {
  return apiDelete<{ messages: McpWireMessage[] }>(
    `/mcp/servers/${encodeURIComponent(id)}/transcript`,
  );
}

export function serverCost(id: string): Promise<McpCost> {
  return apiGet<McpCost>(`/mcp/servers/${encodeURIComponent(id)}/cost`);
}

export function discoverServers(
  q: string,
  limit = 30,
): Promise<{ entries: McpCatalogEntry[]; registryOnline: boolean }> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return apiGet(`/mcp/discover?${params.toString()}`);
}

/** Connect a candidate once and report what it really is. Nothing is saved. */
export function probeServer(input: McpServerInput): Promise<McpProbe> {
  return apiPost<McpProbe>('/mcp/probe', input);
}

/** A catalog entry + one of its install options, as a savable/probeable config. */
export function toServerInput(
  entry: McpCatalogEntry,
  option: McpInstallOption,
  overrides: Partial<McpServerInput> = {},
): McpServerInput {
  const secretEnv = option.env.filter((v) => v.secret).map((v) => v.name);
  return {
    id: entry.suggestedId,
    name: entry.title || entry.name,
    transport: option.transport,
    command: option.command,
    args: option.args,
    url: option.url,
    // Non-secret declared variables with a default are safe in the plaintext config;
    // secret ones are carried by name only and their values go to the secret store.
    env: Object.fromEntries(
      option.env.filter((v) => !v.secret && v.default).map((v) => [v.name, v.default]),
    ),
    secretEnv,
    // Anything reached through Discover is a third party's code, and the pane says so
    // on the row forever after. Labelling it here — at the one moment the provenance
    // is actually known — is the only place it can be done honestly.
    origin: 'registry',
    ...overrides,
  };
}

/** One hand-invocation's result, plus how long the server took to answer. */
export interface McpCallResult {
  content: string;
  structured: unknown;
  attachments: string[];
  error: string | null;
  elapsedMs: number;
}

export type McpCheckStatus = 'pass' | 'warn' | 'fail' | 'skip';

export interface McpConformanceCheck {
  id: string;
  title: string;
  status: McpCheckStatus;
  detail: string;
}

export interface McpConformance {
  status: McpCheckStatus;
  serverName: string;
  serverVersion: string;
  protocolVersion: string;
  checks: McpConformanceCheck[];
}

export type McpTemplate = 'python' | 'node';

export interface McpProject {
  id: string;
  title: string;
  template: McpTemplate;
  state: 'new' | 'provisioning' | 'ready' | 'error';
  error: string;
  root: string;
  entry: string;
  /** False when the source is on disk but no server points at it — see `register`. */
  registered: boolean;
  files: string[];
  log: string[];
}

/** Invoke a tool directly — the same call the agent would make, minus the model. */
export function callTool(
  id: string,
  name: string,
  args: Record<string, unknown>,
): Promise<McpCallResult> {
  return apiPost<McpCallResult>(`/mcp/servers/${encodeURIComponent(id)}/call`, {
    name,
    arguments: args,
  });
}

/** Check a connected server's declarations and protocol edges. Never calls a tool. */
export function runConformance(id: string): Promise<McpConformance> {
  return apiPost<McpConformance>(`/mcp/servers/${encodeURIComponent(id)}/conformance`, {});
}

export function listProjects(): Promise<{
  projects: McpProject[];
  hasUv: boolean;
  hasNpm: boolean;
}> {
  return apiGet('/mcp/projects');
}

export function createProject(
  id: string,
  template: McpTemplate,
  title: string,
): Promise<McpProject> {
  return apiPost<McpProject>('/mcp/projects', { id, template, title });
}

export function provisionProject(id: string): Promise<McpProject> {
  return apiPost<McpProject>(`/mcp/projects/${encodeURIComponent(id)}/provision`, {});
}

export function deleteProject(
  id: string,
  deleteFiles = false,
): Promise<{ projects: McpProject[]; hasUv: boolean; hasNpm: boolean }> {
  const params = new URLSearchParams({ deleteFiles: String(deleteFiles) });
  return apiDelete(`/mcp/projects/${encodeURIComponent(id)}?${params.toString()}`);
}

/** Put an unregistered project back in the server list. */
export function registerProject(id: string): Promise<McpProject> {
  return apiPost<McpProject>(`/mcp/projects/${encodeURIComponent(id)}/register`, {});
}

export function readProjectFile(id: string, path: string): Promise<{ text: string }> {
  const params = new URLSearchParams({ path });
  return apiGet(`/mcp/projects/${encodeURIComponent(id)}/file?${params.toString()}`);
}

/** Save a file; the server restarts if the edit touches what it runs. */
export function writeProjectFile(
  id: string,
  path: string,
  text: string,
): Promise<{ restarted: boolean; restartError: string | null }> {
  return apiPost(`/mcp/projects/${encodeURIComponent(id)}/file`, { path, text });
}

export function readResource(
  id: string,
  uri: string,
): Promise<{ contents: unknown[]; error: string | null }> {
  return apiPost(`/mcp/servers/${encodeURIComponent(id)}/resource`, { uri });
}

/**
 * The MCP server this node *exports* — the other direction, where an external
 * agent asks what this node's agent has been doing.
 *
 * `hasToken` and never the token: it grants read access to this node's
 * trajectories and telemetry, so it appears only when someone explicitly asks
 * (`revealExportToken`).
 */
export interface McpExportStatus {
  enabled: boolean;
  mountPath: string;
  enableEnv: string;
  hasToken: boolean;
  exposeContent: boolean;
}

export function exportStatus(): Promise<McpExportStatus> {
  return apiGet<McpExportStatus>('/mcp/export');
}

/** Reveal the bearer token, or mint a replacement when `rotate` is set. */
export function revealExportToken(rotate = false): Promise<{ token: string; mountPath: string }> {
  return apiPost<{ token: string; mountPath: string }>(
    `/mcp/export/token${rotate ? '?rotate=true' : ''}`,
    {},
  );
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
