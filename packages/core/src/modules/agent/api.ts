import { apiGet, apiPut } from '../../api';
import { apiUrl } from '../../origin';

export interface AgentStatus {
  ollama_reachable: boolean;
  configured: boolean;
  model: string | null;
  endpoint: string;
  available_models: string[];
}

export const DEFAULT_AGENT_MODEL = 'gemma4:e2b';

export function getAgentStatus(): Promise<AgentStatus> {
  return apiGet<AgentStatus>('/agent/status');
}

export function saveAgentConfig(model: string, endpoint?: string): Promise<unknown> {
  return apiPut('/agent/config', endpoint ? { model, endpoint } : { model });
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
