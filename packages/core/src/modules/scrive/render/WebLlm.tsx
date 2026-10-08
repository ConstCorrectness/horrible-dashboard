/**
 * `{webllm} owner/model`: a language model that runs in the page, on the reader's GPU.
 *
 *   ```{webllm} onnx-community/Qwen3-0.6B-ONNX
 *   :system: You are a terse assistant.
 *   :show: chat, tokens
 *   ```
 *
 * In the app this is the app-global WebML engine (the same loaded model as the
 * playground and the `browser` chat provider). Nothing downloads until someone asks:
 * the block says how big the model is first. With `tokens` in `:show:`, each reply
 * comes with its token-probability strip, and the author can keep a reply as a
 * `{tokenviz}` figure — the recorded version readers see without downloading anything.
 *
 * A published page embeds `_scrive/webml/embed.html` instead (the same idea in plain
 * JS, transformers.js from a CDN), on the blog's own origin so its cache persists.
 */
import { useEffect, useRef, useState } from 'react';

import {
  catalogEntry,
  formatBytes,
  isModelCached,
  pickDtype,
  type ChatMessage,
  type Dtype,
} from '@horrible/webml';

import { type TokenRun, type TokenStep, TokenStrip } from '../../../token-strip/TokenStrip';
import { useEngineState, useGpuReport, webmlEngine } from '../../webml';

export interface WebLlmOptions {
  model: string;
  dtype?: Dtype;
  system: string;
  showTokens: boolean;
  maxTokens: number;
}

export function webLlmOptions(arg: unknown, options: Record<string, unknown>): WebLlmOptions {
  const show = String(options.show ?? 'chat')
    .split(/[\s,]+/)
    .map((s) => s.trim().toLowerCase());
  const dtype = String(options.dtype ?? '').trim();
  return {
    model: String(arg ?? '').trim(),
    dtype: (['q4f16', 'q4', 'fp16', 'fp32'] as const).find((d) => d === dtype),
    system: String(options.system ?? '').trim(),
    showTokens: show.includes('tokens'),
    maxTokens: Math.max(16, Math.min(2048, Number(options.max) || 256)),
  };
}

interface Turn {
  role: 'user' | 'assistant';
  content: string;
  steps?: TokenStep[];
  meta?: string;
}

export function WebLlm({
  arg,
  options,
  onKeep,
}: {
  arg: unknown;
  options: Record<string, unknown>;
  /** Keep a reply as a `{tokenviz}` figure (Write mode). */
  onKeep?: (run: TokenRun) => void;
}) {
  const opts = webLlmOptions(arg, options);
  const gpu = useGpuReport();
  const state = useEngineState();
  const entry = catalogEntry(opts.model);
  const dtype = opts.dtype ?? pickDtype(entry, gpu?.available ? gpu.f16 : false);
  const bytes = entry?.sizes[dtype];
  const [cached, setCached] = useState<boolean | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    let live = true;
    void isModelCached(opts.model).then(
      (c) => live && setCached(c),
      () => live && setCached(false),
    );
    return () => {
      live = false;
    };
  }, [opts.model, state.kind]);

  const ready = state.kind === 'ready' && state.model === opts.model;
  const loading = state.kind === 'loading' && state.model === opts.model;

  if (!opts.model) {
    return (
      <figure className="scrive-llm" data-status="error">
        <div className="scrive-space-error">
          {'{webllm}'} needs a Hugging Face model id (owner/name)
        </div>
      </figure>
    );
  }

  const load = async () => {
    setError(null);
    try {
      await webmlEngine().load(opts.model, dtype, gpu?.available ? 'webgpu' : 'wasm');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || !ready || busy) return;
    setDraft('');
    setError(null);
    const history: ChatMessage[] = turns.map((t) => ({ role: t.role, content: t.content }));
    const prompt: ChatMessage[] = [
      ...(opts.system ? [{ role: 'system' as const, content: opts.system }] : []),
      ...history,
      { role: 'user', content: text },
    ];
    setTurns((t) => [
      ...t,
      { role: 'user', content: text },
      { role: 'assistant', content: '', steps: [] },
    ]);
    const patch = (fn: (t: Turn) => Turn) =>
      setTurns((all) => [...all.slice(0, -1), fn(all[all.length - 1])]);
    const ctrl = new AbortController();
    abort.current = ctrl;
    setBusy(true);
    try {
      const result = await webmlEngine().generate(
        {
          messages: prompt,
          maxNewTokens: opts.maxTokens,
          topK: opts.showTokens ? 5 : 0,
          temperature: 0.7,
          templateKwargs: entry?.thinking ? { enable_thinking: false } : undefined,
        },
        {
          signal: ctrl.signal,
          onDelta: (d) => patch((t) => ({ ...t, content: t.content + d })),
          onStep: (s) =>
            patch((t) => ({
              ...t,
              steps: [
                ...(t.steps ?? []),
                { token: s.token, p: s.p, entropy: s.entropy, topk: s.topk },
              ],
            })),
        },
      );
      patch((t) => ({
        ...t,
        content: result.text,
        meta: `${result.usage.completionTokens} tokens · ${result.tokensPerSecond.toFixed(1)} tok/s`,
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      abort.current = null;
      setBusy(false);
    }
  };

  const label = entry ? `${entry.label} · ${entry.params}` : opts.model;
  return (
    <figure className="scrive-llm" data-status={error ? 'error' : 'ready'}>
      <figcaption className="scrive-space-bar">
        <span className="scrive-space-title">{label}</span>
        <span className="scrive-space-meta">
          {gpu && !gpu.available
            ? `no WebGPU here (${gpu.reason})`
            : ready
              ? `running on this GPU · ${state.kind === 'ready' ? state.dtype : ''}`
              : `in-browser model · ${dtype}${bytes ? ` · ${formatBytes(bytes)}` : ''}`}
        </span>
      </figcaption>
      {!ready ? (
        <div className="scrive-llm-gate">
          <button type="button" onClick={() => void load()} disabled={loading || gpu === null}>
            {loading
              ? 'Loading…'
              : cached
                ? 'Run it here (cached on this device)'
                : `Run it here${bytes ? ` (downloads ${formatBytes(bytes)} once)` : ''}`}
          </button>
          {state.kind === 'ready' && state.model !== opts.model && (
            <span className="scrive-space-note">
              Loading replaces {state.model.split('/').pop()}, which is loaded now.
            </span>
          )}
        </div>
      ) : (
        <>
          <div className="scrive-llm-turns">
            {turns.map((t, i) => (
              <div key={i} className={`scrive-llm-turn is-${t.role}`}>
                <span className="scrive-llm-role">{t.role === 'user' ? 'You' : 'Model'}</span>
                <div>
                  <div className="scrive-llm-text">
                    {t.content || (busy && i === turns.length - 1 ? '…' : '')}
                  </div>
                  {opts.showTokens &&
                    t.steps &&
                    t.steps.length > 0 &&
                    !(busy && i === turns.length - 1) && (
                      <div className="scrive-tokenviz-strip">
                        <TokenStrip steps={t.steps} />
                      </div>
                    )}
                  {t.meta && (
                    <div className="scrive-space-note">
                      {t.meta}
                      {onKeep && t.steps && t.steps.length > 0 && (
                        <>
                          {' · '}
                          <button
                            type="button"
                            className="scrive-app-tool"
                            onClick={() =>
                              onKeep({
                                model: opts.model,
                                prompt: turns[i - 1]?.content ?? '',
                                steps: t.steps ?? [],
                              })
                            }
                          >
                            Keep as figure
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
          <form
            className="scrive-llm-ask"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <input
              type="text"
              aria-label="Message the model"
              placeholder="Ask the model"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            {busy ? (
              <button type="button" onClick={() => abort.current?.abort()}>
                Stop
              </button>
            ) : (
              <button type="submit" disabled={!draft.trim()}>
                Send
              </button>
            )}
          </form>
        </>
      )}
      {(error || (state.kind === 'error' && state.model === opts.model)) && (
        <div className="scrive-space-error">
          {error ?? (state.kind === 'error' ? state.message : '')}
        </div>
      )}
    </figure>
  );
}
