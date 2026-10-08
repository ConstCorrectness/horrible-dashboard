/**
 * The app's one in-browser model engine.
 *
 * App-global on purpose, like notebook kernels: a loaded model is a gigabyte of GPU
 * memory and seconds of shader compilation, and the playground, the `browser` chat
 * provider and Scrive's `{webllm}` blocks all want the *same* loaded model. Closing a
 * pane therefore never unloads it — `webml.unload` (or loading another) does.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';

import {
  listCachedModels,
  parseNodeGgufModelId,
  probeWebGpu,
  WebmlEngine,
  workerFor,
  type CachedModel,
  type EngineState,
  type GenerateHandlers,
  type GenerateOptions,
  type GenerationResult,
  type GpuReport,
} from '@horrible/webml';

import { apiUrl } from '../../origin';
import { lensRuns } from './lens-run';

let engine: WebmlEngine | null = null;

/** Where a `gguf-node:` model's bytes are: this node's catalog file route. */
export function nodeGgufUrl(model: string): string | null {
  const ref = parseNodeGgufModelId(model);
  return ref ? apiUrl(`/api/llamacpp/models/file?path=${encodeURIComponent(ref.path)}`) : null;
}

/**
 * The engine, keeping the latest logit-lens run (`lens-run.ts`) whichever surface
 * asked for it, so the model explorer can draw it without each caller reporting in.
 */
export class AppEngine extends WebmlEngine {
  override generate(
    options: GenerateOptions,
    handlers: GenerateHandlers = {},
  ): Promise<GenerationResult> {
    const state = this.getState();
    // A forced run is an analysis of given text (the Lens Lab's comparison), not a
    // reply, so it does not replace the reply being looked at.
    if (!options.lens || options.forced !== undefined || state.kind !== 'ready' || this.busy) {
      return super.generate(options, handlers);
    }
    lensRuns.start(state.model);
    return super
      .generate(options, {
        ...handlers,
        onStep: (step) => {
          lensRuns.push(step);
          handlers.onStep?.(step);
        },
      })
      .finally(() => lensRuns.finish());
  }
}

export function webmlEngine(): WebmlEngine {
  engine ??= new AppEngine(workerFor, { sourceUrl: nodeGgufUrl });
  return engine;
}

export function useEngineState(): EngineState {
  const e = webmlEngine();
  return useSyncExternalStore(e.subscribe, e.getState);
}

/** The GPU report, or null while the probe runs. */
export function useGpuReport(): GpuReport | null {
  const [report, setReport] = useState<GpuReport | null>(null);
  useEffect(() => {
    let live = true;
    void probeWebGpu().then((r) => live && setReport(r));
    return () => {
      live = false;
    };
  }, []);
  return report;
}

/** Cached models, re-read whenever `version` changes (bump it after a load or delete). */
export function useCachedModels(version: unknown): CachedModel[] | null {
  const [models, setModels] = useState<CachedModel[] | null>(null);
  useEffect(() => {
    let live = true;
    void listCachedModels()
      .then((m) => live && setModels(m))
      .catch(() => live && setModels([]));
    return () => {
      live = false;
    };
  }, [version]);
  return models;
}
