import { apiGet, apiPost, apiPut } from '../../api';
import { apiUrl } from '../../origin';

/** One auto-detected local-model provider (Ollama, LM Studio, vLLM). */
export interface DetectedProvider {
  kind: string;
  label: string;
  endpoint: string;
  reachable: boolean;
  models: string[];
  can_pull: boolean;
  can_spawn: boolean;
  install_url: string;
}

/** Lifecycle of an optional backend-spawned vLLM server. */
export interface VllmStatus {
  available: boolean;
  running: boolean;
  model: string | null;
  endpoint: string;
  pid: number | null;
  logs: string[];
}

export interface AgentStatus {
  configured: boolean;
  /** Configured provider kind, or null before onboarding. */
  provider: string | null;
  model: string | null;
  /** Active (configured) provider endpoint. */
  endpoint: string;
  /** Whether the active provider is reachable right now. */
  reachable: boolean;
  /** Models on the active provider. */
  available_models: string[];
  /** Every provider we probed, for the onboarding picker. */
  providers: DetectedProvider[];
  vllm: VllmStatus;
}

export const DEFAULT_AGENT_MODEL = 'gemma4:e2b';
/** A small Gemma served well by vLLM; a sensible default for the spawn flow. */
export const DEFAULT_VLLM_MODEL = 'google/gemma-2-2b-it';

export function getAgentStatus(): Promise<AgentStatus> {
  return apiGet<AgentStatus>('/agent/status');
}

/** One roster agent (a fully separate loop: own prompt, tool scope, settings,
 * sessions). `tool_groups: null` = unrestricted (the main orchestrator). */
export interface RosterAgent {
  id: string;
  name: string;
  description: string;
  tool_groups: string[] | null;
  default_mode: string;
}

export function getAgentRoster(): Promise<RosterAgent[]> {
  return apiGet<{ agents: RosterAgent[] }>('/agent/roster').then((r) => r.agents);
}

export function saveAgentConfig(
  model: string,
  provider: string,
  endpoint?: string,
): Promise<unknown> {
  return apiPut('/agent/config', { model, provider, ...(endpoint ? { endpoint } : {}) });
}

/** Spawn a backend vLLM server to serve `model`; returns the new vLLM status. */
export function spawnVllm(model: string, port?: number): Promise<VllmStatus> {
  return apiPost<VllmStatus>('/agent/vllm/spawn', port ? { model, port } : { model });
}

/** Stop the backend-spawned vLLM server, if any. */
export function stopVllm(): Promise<VllmStatus> {
  return apiPost<VllmStatus>('/agent/vllm/stop', {});
}

async function streamNdjson(
  path: string,
  body: unknown,
  onLine: (obj: Record<string, unknown>) => void,
): Promise<void> {
  const res = await fetch(apiUrl(`/api${path}`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) throw new Error(`API POST ${path} failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.trim()) onLine(JSON.parse(line) as Record<string, unknown>);
    }
  }
}

/** Stream a one-shot answer from the configured local model; resolves when done. */
export function streamAgentChat(prompt: string, onToken: (token: string) => void): Promise<void> {
  return streamNdjson('/agent/chat', { prompt }, (obj) => {
    if (typeof obj.response === 'string') onToken(obj.response);
    if (typeof obj.error === 'string') throw new Error(obj.error);
  });
}

/** A fill-in completion request, optionally grounded with LSP context (the symbols
 * in scope and the type/signature at the cursor) so the model's ghost text resolves. */
export interface CompleteParams {
  prefix: string;
  suffix: string;
  language?: string;
  /** Completion-item labels the language server reports at the cursor. */
  completions?: string[];
  /** Hover text (the expected type / signature) at the cursor. */
  hover?: string;
}

/** One short fill-in completion for the editor's inline autosuggest. */
export function completeCode(params: CompleteParams, signal?: AbortSignal): Promise<string> {
  const { prefix, suffix, language, completions, hover } = params;
  return apiPost<{ completion: string }>(
    '/agent/complete',
    {
      prefix,
      suffix,
      ...(language ? { language } : {}),
      ...(completions && completions.length ? { completions } : {}),
      ...(hover ? { hover } : {}),
    },
    signal,
  ).then((r) => r.completion);
}

export interface PullProgress {
  status?: string;
  completed?: number;
  total?: number;
  error?: string;
}

/** Pull a model through the backend; progress callbacks mirror Ollama's events. */
export function pullAgentModel(
  model: string,
  onProgress: (progress: PullProgress) => void,
): Promise<void> {
  return streamNdjson('/agent/pull', { model }, (obj) => {
    const p = obj as PullProgress;
    if (p.error) throw new Error(p.error);
    onProgress(p);
  });
}
