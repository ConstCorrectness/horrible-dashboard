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
  type GpuReport,
} from '@horrible/webml';

import { apiUrl } from '../../origin';

let engine: WebmlEngine | null = null;

/** Where a `gguf-node:` model's bytes are: this node's catalog file route. */
export function nodeGgufUrl(model: string): string | null {
  const ref = parseNodeGgufModelId(model);
  return ref ? apiUrl(`/api/llamacpp/models/file?path=${encodeURIComponent(ref.path)}`) : null;
}

export function webmlEngine(): WebmlEngine {
  engine ??= new WebmlEngine(workerFor, { sourceUrl: nodeGgufUrl });
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
