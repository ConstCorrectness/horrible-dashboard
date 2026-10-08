/**
 * The window's half of the `browser` chat provider (backend/modules/agent/
 * browser_provider.py). The agent loop runs in the backend; when a round is for an
 * in-browser model it arrives here as `generate_request` on the `agent` channel, runs
 * on the app-global engine, and streams back as `generate_delta` → `generate_done`
 * (or `generate_error`). `generate_cancel` is the chat's Stop.
 *
 * It also keeps the backend told what this window can run (`webml_manifest`): whether
 * it has WebGPU, which models are cached, which is loaded. That is what makes the
 * provider show as reachable — and only with models that need no download.
 */
import {
  catalogEntry,
  ggufSuggestion,
  parseGgufModelId,
  listCachedModels,
  pickDtype,
  probeWebGpu,
  type ChatMessage,
} from '@horrible/webml';

import { getSetting } from '../../settings';
import { onSocketOpen, sendChannel, subscribeChannel } from '../../ws';
import { webmlEngine } from './engine';

interface GenerateRequestData {
  genId: string;
  model: string;
  messages: ChatMessage[];
  tools?: unknown[];
  temperature?: number | null;
  maxTokens?: number | null;
}

const live = new Map<string, AbortController>();
let started = false;

export async function webmlManifest(): Promise<{
  available: boolean;
  reason?: string;
  models: string[];
  loaded: string | null;
}> {
  const gpu = await probeWebGpu();
  const state = webmlEngine().getState();
  const loaded = state.kind === 'ready' ? state.model : null;
  let models: string[] = [];
  try {
    // A partial GGUF download is on disk but cannot run until it is resumed.
    models = (await listCachedModels()).filter((m) => !m.partial).map((m) => m.id);
  } catch {
    // No Cache Storage here: only the loaded model is runnable without a download.
  }
  return gpu.available
    ? { available: true, models, loaded }
    : { available: false, reason: gpu.reason, models: [], loaded: null };
}

async function pushManifest(): Promise<void> {
  sendChannel('agent', 'webml_manifest', await webmlManifest());
}

async function run(data: GenerateRequestData): Promise<void> {
  const { genId, model } = data;
  const engine = webmlEngine();
  const ctrl = new AbortController();
  live.set(genId, ctrl);
  try {
    const state = engine.getState();
    const entry = catalogEntry(model);
    if (state.kind !== 'ready' || state.model !== model) {
      const gpu = await probeWebGpu();
      await engine.load(
        model,
        pickDtype(entry, gpu.available && gpu.f16),
        gpu.available ? 'webgpu' : 'wasm',
      );
    }
    // Tools go to the template only for models that write a format we parse; a
    // catalog model without one would just be confused by a long tool preamble.
    // A suggested GGUF repo says the same; any other GGUF's template decides.
    const ref = parseGgufModelId(model);
    const toolFormat = entry
      ? entry.toolFormat
      : ref
        ? ggufSuggestion(ref.repo)?.toolFormat
        : undefined;
    const tools = data.tools?.length && toolFormat !== null ? data.tools : undefined;
    const loaded = engine.getState();
    const thinking = entry?.thinking ?? (loaded.kind === 'ready' && loaded.thinking === true);
    const result = await engine.generate(
      {
        messages: data.messages,
        tools,
        temperature: data.temperature ?? undefined,
        maxNewTokens: data.maxTokens ?? 2048,
        templateKwargs: thinking
          ? { enable_thinking: getSetting<boolean>('webml.thinking') ?? false }
          : undefined,
      },
      {
        signal: ctrl.signal,
        onDelta: (text) => sendChannel('agent', 'generate_delta', { genId, text }),
      },
    );
    sendChannel('agent', 'generate_done', {
      genId,
      text: result.text,
      stop: result.stop,
      usage: result.usage,
    });
  } catch (err) {
    sendChannel('agent', 'generate_error', {
      genId,
      message: err instanceof Error ? err.message : String(err),
    });
  } finally {
    live.delete(genId);
  }
}

/** Idempotent. Call once at boot, beside `initAgentRelay`. */
export function initWebmlRelay(): void {
  if (started) return;
  started = true;
  onSocketOpen(() => void pushManifest());
  // A load or unload changes what is runnable without a download.
  let lastKey = '';
  webmlEngine().subscribe(() => {
    const s = webmlEngine().getState();
    const key = s.kind === 'ready' ? `ready:${s.model}` : s.kind === 'loading' ? 'loading' : s.kind;
    if (key === lastKey || key === 'loading') return;
    lastKey = key;
    void pushManifest();
  });
  subscribeChannel('agent', (msg) => {
    const data = (msg.data ?? {}) as Record<string, unknown>;
    if (msg.event === 'generate_request') {
      void run(data as unknown as GenerateRequestData);
    } else if (msg.event === 'generate_cancel') {
      live.get(String(data.genId))?.abort();
    }
  });
}

/** Re-announce after the cache changes outside a load (a delete in the playground). */
export function refreshWebmlManifest(): void {
  if (started) void pushManifest();
}
