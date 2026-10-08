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
  probeWebGpu,
  WebmlEngine,
  type CachedModel,
  type EngineState,
  type GpuReport,
} from '@horrible/webml';

let engine: WebmlEngine | null = null;

export function webmlEngine(): WebmlEngine {
  engine ??= new WebmlEngine();
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
