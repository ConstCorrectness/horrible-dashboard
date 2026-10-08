/**
 * WebML playground: load a model into this window's GPU, talk to it, and watch
 * how sure it was of every token. The model is the app-global one (`engine.ts`),
 * so whatever is loaded here is also what the `browser` chat provider answers with.
 *
 * Two kinds of model: the ONNX catalog (or any ONNX Hub repo), run by
 * transformers.js, and GGUF files from the Hub, run by our own WGSL engine
 * (docs/architecture/webml-gguf-engine.mdx).
 */
import { useMemo, useRef, useState } from 'react';

import {
  CATALOG,
  catalogEntry,
  deleteCachedModel,
  formatBytes,
  isGgufModelId,
  loadTotals,
  pickDtype,
  type ChatMessage,
  type Dtype,
} from '@horrible/webml';

import { useAgentContext } from '../../../agent-context';
import { useSetting } from '../../../settings';
import { useCachedModels, useEngineState, useGpuReport, webmlEngine } from '../engine';
import {
  playground,
  splitThinking,
  usePlayground,
  type PlaygroundMessage,
} from '../playground-state';
import { refreshWebmlManifest } from '../relay';
import { useRollingNumber } from '../rolling';
import { GgufPicker, type GgufChoice } from './GgufPicker';
import { TokenStrip } from '../../../token-strip/TokenStrip';

import '../webml.css';

const CUSTOM = '__custom__';
const GGUF = '__gguf__';
const ALL_DTYPES: Dtype[] = ['q4f16', 'q4', 'fp16', 'fp32'];

export function PlaygroundPanel() {
  const gpu = useGpuReport();
  const state = useEngineState();
  const { messages, streaming } = usePlayground();
  const defaultModel = useSetting<string>('webml.defaultModel') ?? CATALOG[1].id;
  const topK = useSetting<number>('webml.topk') ?? 5;
  const temperature = useSetting<number>('webml.temperature') ?? 0.7;
  const thinkingOn = useSetting<boolean>('webml.thinking') ?? false;

  const [choice, setChoice] = useState(() =>
    catalogEntry(defaultModel) ? defaultModel : isGgufModelId(defaultModel) ? GGUF : CUSTOM,
  );
  const [custom, setCustom] = useState(() =>
    catalogEntry(defaultModel) || isGgufModelId(defaultModel) ? '' : defaultModel,
  );
  const [gguf, setGguf] = useState<GgufChoice>({ id: '', inspection: null });
  const isGguf = choice === GGUF;
  const modelId = isGguf ? gguf.id : choice === CUSTOM ? custom.trim() : choice;
  const entry = catalogEntry(modelId);
  const f16 = gpu?.available ? gpu.f16 : false;
  const offered: Dtype[] = entry ? (Object.keys(entry.sizes) as Dtype[]) : ALL_DTYPES;
  const [dtypeChoice, setDtypeChoice] = useState<Dtype | ''>('');
  const dtype = dtypeChoice && offered.includes(dtypeChoice) ? dtypeChoice : pickDtype(entry, f16);

  const [system, setSystem] = useState('');
  const [draft, setDraft] = useState('');
  const [cacheVersion, setCacheVersion] = useState(0);
  const cached = useCachedModels(cacheVersion);
  const abort = useRef<AbortController | null>(null);

  const isCached = cached?.some((m) => m.id === modelId && !m.partial) ?? false;
  const downloadBytes = isGguf ? gguf.inspection?.size : entry?.sizes[dtype];
  const gpuReady = gpu?.available === true;
  const ready = state.kind === 'ready';
  const busy = streaming !== -1;
  // A GGUF loads once its header says it can run here (or it is already on disk).
  const canLoad = isGguf ? gpuReady && (isCached || gguf.inspection?.ok === true) : true;
  // Reasoning switch: the catalog says, or a loaded GGUF's template does.
  const thinkingModel =
    entry?.thinking ?? (ready && state.model === modelId && state.thinking === true);

  const last = useMemo(
    () => [...messages].reverse().find((m) => m.role === 'assistant' && m.meta),
    [messages],
  );
  const tokS = useRollingNumber(last?.meta?.tokensPerSecond ?? 0);
  const ttft = useRollingNumber(last?.meta?.ttftMs ?? 0);

  useAgentContext(() => ({
    webgpu: gpu,
    engine:
      state.kind === 'ready'
        ? {
            model: state.model,
            engine: state.engine,
            weights: state.quant ?? state.dtype,
            loadMs: state.loadMs,
          }
        : state.kind,
    lastReply: last?.meta ?? null,
    turns: messages.length,
  }));

  const load = async () => {
    if (!modelId) return;
    try {
      // GGUF weights live in OPFS; ask the browser not to evict them under pressure.
      if (isGguf) await navigator.storage?.persist?.().catch(() => false);
      await webmlEngine().load(modelId, dtype, gpuReady ? 'webgpu' : 'wasm');
    } catch {
      // The engine state carries the message.
    } finally {
      setCacheVersion((v) => v + 1);
    }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || state.kind !== 'ready' || busy) return;
    setDraft('');
    const history: ChatMessage[] = messages
      .filter((m) => !m.error)
      .map((m) => ({ role: m.role, content: m.content }));
    const prompt: ChatMessage[] = [
      ...(system.trim() ? [{ role: 'system' as const, content: system.trim() }] : []),
      ...history,
      { role: 'user', content: text },
    ];
    const index = playground.begin(text);
    const ctrl = new AbortController();
    abort.current = ctrl;
    const model = state.model;
    const thinking = catalogEntry(model)?.thinking ?? state.thinking === true;
    try {
      const result = await webmlEngine().generate(
        {
          messages: prompt,
          topK,
          temperature,
          templateKwargs: thinking ? { enable_thinking: thinkingOn } : undefined,
        },
        {
          signal: ctrl.signal,
          onDelta: (delta) =>
            playground.patch(index, (m) => ({ ...m, content: m.content + delta })),
          onStep: (step) =>
            playground.patch(index, (m) => ({
              ...m,
              steps: [
                ...(m.steps ?? []),
                { token: step.token, p: step.p, entropy: step.entropy, topk: step.topk },
              ],
            })),
        },
      );
      playground.patch(index, (m) => ({
        ...m,
        // The streamer holds back a partial word; the final text is authoritative.
        content: result.text,
        meta: {
          ttftMs: result.ttftMs,
          tokensPerSecond: result.tokensPerSecond,
          tokens: result.usage.completionTokens,
          stop: result.stop,
          model,
        },
      }));
    } catch (err) {
      playground.patch(index, (m) => ({
        ...m,
        error: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      abort.current = null;
      playground.end();
    }
  };

  const remove = async (id: string) => {
    await deleteCachedModel(id);
    setCacheVersion((v) => v + 1);
    refreshWebmlManifest();
  };

  const progress = state.kind === 'loading' ? loadTotals(state.files) : null;
  const pct =
    progress && progress.total > 0 ? Math.min(100, (progress.loaded / progress.total) * 100) : 0;

  return (
    <div className="webml">
      <header className="webml-head">
        <span className="webml-title">WebML</span>
        {gpu === null ? (
          <span className="webml-meta">probing GPU…</span>
        ) : gpu.available ? (
          <>
            <span className="webml-meta">
              {[gpu.vendor, gpu.architecture].filter(Boolean).join(' · ') || 'GPU adapter'}
              {gpu.isFallback ? ' (software fallback)' : ''}
            </span>
            <span className={`webml-flag${gpu.f16 ? ' is-on' : ''}`}>f16</span>
            <span className={`webml-flag${gpu.subgroups ? ' is-on' : ''}`}>subgroups</span>
            <span className="webml-meta">max buffer {formatBytes(gpu.maxBufferSize)}</span>
          </>
        ) : (
          <span className="webml-flag is-bad" title={gpu.reason}>
            no WebGPU — {gpu.reason}
          </span>
        )}
      </header>

      <div className="webml-body">
        <aside className="webml-side">
          <section className="webml-card">
            <span className="webml-label">Model</span>
            <div className="webml-row">
              <select
                aria-label="Model"
                value={choice}
                onChange={(e) => {
                  setChoice(e.target.value);
                  setDtypeChoice('');
                }}
              >
                {CATALOG.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label} · {m.params}
                  </option>
                ))}
                <option value={CUSTOM}>Other Hub model…</option>
                <option value={GGUF}>GGUF file from the Hub…</option>
              </select>
            </div>
            {isGguf && <GgufPicker initial={defaultModel} onChange={setGguf} />}
            {choice === CUSTOM && (
              <div className="webml-row">
                <input
                  type="text"
                  aria-label="Hub model id"
                  placeholder="owner/name (ONNX, text generation)"
                  value={custom}
                  onChange={(e) => setCustom(e.target.value)}
                />
              </div>
            )}
            {!isGguf && (
              <div className="webml-row">
                <select
                  aria-label="Weights"
                  value={dtype}
                  onChange={(e) => setDtypeChoice(e.target.value as Dtype)}
                >
                  {offered.map((d) => (
                    <option key={d} value={d} disabled={d.endsWith('f16') && !f16}>
                      {d}
                      {entry?.sizes[d] ? ` · ${formatBytes(entry.sizes[d]!)}` : ''}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <span className="webml-meta">
              {isCached
                ? 'cached on this device'
                : downloadBytes
                  ? `downloads ${formatBytes(downloadBytes)} from huggingface.co`
                  : 'downloads from huggingface.co'}
              {isGguf && !gpuReady ? ' · GGUF models need WebGPU' : ''}
              {entry ? ` · ${entry.license}` : ''}
              {entry?.toolFormat ? ' · tools' : ''}
            </span>
            <div className="webml-row">
              <button
                type="button"
                onClick={() => void load()}
                disabled={!modelId || !canLoad || state.kind === 'loading' || busy}
              >
                {ready && state.model === modelId && (isGguf || state.dtype === dtype)
                  ? 'Loaded'
                  : 'Load'}
              </button>
              <button
                type="button"
                onClick={() => webmlEngine().unload()}
                disabled={state.kind !== 'ready' || busy}
              >
                Unload
              </button>
            </div>
            {state.kind === 'loading' && (
              <>
                <div className="webml-bar" role="progressbar" aria-valuenow={Math.round(pct)}>
                  <div
                    style={{ transform: `scaleX(${(state.phase === 'warmup' ? 100 : pct) / 100})` }}
                  />
                </div>
                <span className="webml-meta">
                  {state.phase === 'warmup'
                    ? isGgufModelId(state.model)
                      ? 'uploading weights to the GPU…'
                      : 'compiling GPU kernels…'
                    : `${formatBytes(progress?.loaded ?? 0)} / ${formatBytes(progress?.total ?? 0)} · ${Object.keys(state.files).length} files`}
                </span>
              </>
            )}
            {state.kind === 'ready' && (
              <span className="webml-meta">
                ready · {state.model.split('/').pop()} ·{' '}
                {state.engine === 'gguf'
                  ? `${state.quant ?? 'GGUF'} · ${state.contextLength?.toLocaleString() ?? '?'} context`
                  : state.dtype}{' '}
                · {state.device} ·{' '}
                {state.loadMs ? `${(state.loadMs / 1000).toFixed(1)} s` : 'already loaded'}
              </span>
            )}
            {state.kind === 'error' && <span className="webml-error">{state.message}</span>}
          </section>

          <section className="webml-card">
            <span className="webml-label">System prompt</span>
            <div className="webml-row">
              <input
                type="text"
                aria-label="System prompt"
                placeholder="optional"
                value={system}
                onChange={(e) => setSystem(e.target.value)}
              />
            </div>
            <span className="webml-meta">
              temperature {temperature} · top-k shown {topK}
              {thinkingModel ? ` · thinking ${thinkingOn ? 'on' : 'off'}` : ''}
            </span>
          </section>

          <section className="webml-card">
            <span className="webml-label">On this device</span>
            {cached === null ? (
              <span className="webml-meta">reading cache…</span>
            ) : cached.length === 0 ? (
              <span className="webml-meta">no models cached</span>
            ) : (
              <ul className="webml-cache">
                {cached.map((m) => (
                  <li key={m.id}>
                    <span className="webml-cache-id" title={m.id}>
                      {m.id}
                    </span>
                    <span className="webml-meta">
                      {m.partial ? 'partial · ' : ''}
                      {m.bytes ? formatBytes(m.bytes) : `${m.files} files`}
                    </span>
                    <button
                      type="button"
                      className="btn-mini"
                      onClick={() => void remove(m.id)}
                      disabled={state.kind === 'ready' && state.model === m.id}
                      title={`Delete ${m.id} from this device`}
                    >
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>

        <main className="webml-main">
          <div className="webml-stats">
            <Stat label="tok/s" value={last ? tokS.toFixed(1) : '—'} />
            <Stat label="TTFT" value={last ? `${Math.round(ttft)} ms` : '—'} />
            <Stat label="Tokens" value={last?.meta ? String(last.meta.tokens) : '—'} />
            <Stat label="Stop" value={last?.meta?.stop ?? '—'} />
          </div>

          <div className="webml-transcript">
            {messages.length === 0 ? (
              <p className="webml-empty">
                {ready
                  ? 'The model runs on this machine’s GPU. Nothing you type leaves the window.'
                  : 'Load a model to start. Weights download once and stay in this browser’s storage.'}
              </p>
            ) : (
              messages.map((m, i) => (
                <Message key={i} message={m} live={i === streaming} index={i} />
              ))
            )}
          </div>

          <form
            className="webml-composer"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <textarea
              aria-label="Message"
              placeholder={
                ready
                  ? 'Message the model (Enter to send, Shift+Enter for a new line)'
                  : 'Load a model first'
              }
              value={draft}
              disabled={!ready}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="webml-composer-actions">
              {busy ? (
                <button type="button" onClick={() => abort.current?.abort()}>
                  Stop
                </button>
              ) : (
                <button type="submit" disabled={!ready || !draft.trim()}>
                  Send
                </button>
              )}
              <button
                type="button"
                onClick={() => playground.clear()}
                disabled={busy || messages.length === 0}
              >
                Clear
              </button>
            </div>
          </form>
        </main>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="webml-stat">
      <div className="webml-label">{label}</div>
      <div className="webml-stat-value">{value}</div>
    </div>
  );
}

function Message({
  message,
  live,
  index,
}: {
  message: PlaygroundMessage;
  live: boolean;
  index: number;
}) {
  const { thinking, answer } =
    message.role === 'assistant'
      ? splitThinking(message.content)
      : { thinking: null, answer: message.content };
  return (
    <div
      className={`webml-msg is-${message.role}`}
      // Staggered arrival, capped so a long restored transcript is not slow to appear.
      style={{ animationDelay: `${Math.min(index, 8) * 30}ms` }}
    >
      <span className="webml-msg-role">{message.role === 'user' ? 'You' : 'Model'}</span>
      <div>
        {thinking !== null && (
          <details className="webml-think" open={live && !answer}>
            <summary>Thinking</summary>
            <div className="webml-msg-text">{thinking}</div>
          </details>
        )}
        <div className="webml-msg-text">{answer || (live ? '…' : '')}</div>
        {message.error && <div className="webml-error">{message.error}</div>}
        {message.steps && message.steps.length > 0 && !live && (
          <details style={{ marginTop: 6 }}>
            <summary className="webml-label" style={{ cursor: 'pointer' }}>
              Token probabilities
            </summary>
            <TokenStrip steps={message.steps} />
          </details>
        )}
        {message.meta && (
          <div className="webml-msg-meta">
            {message.meta.tokens} tokens · {message.meta.tokensPerSecond.toFixed(1)} tok/s · TTFT{' '}
            {message.meta.ttftMs} ms · {message.meta.stop}
          </div>
        )}
      </div>
    </div>
  );
}
