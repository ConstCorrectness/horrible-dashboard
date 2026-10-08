/**
 * The GGUF model worker: our own WGSL engine, off the UI thread. It speaks the
 * same protocol as transformers.worker.ts (../protocol.ts), so the playground, the
 * `browser` chat provider and `{webllm}` blocks drive it unchanged; the client
 * picks this worker for `gguf:` model ids.
 *
 * One model at a time, one generation at a time (a second `generate` while one
 * runs is refused, not queued). Weights come from the OPFS store, downloaded from
 * the Hub on first load after the header has been checked.
 */
import { requestWebmlDevice, type WebmlDevice } from '../gpu/device';
import { readGgufHeader } from '../gguf/parse';
import { GgufSession, preflight } from '../gguf/session';
import { blobSource, HttpSource, hfResolveUrl } from '../gguf/source';
import { downloadGguf, openGguf, parseGgufModelId } from '../gguf/store';
import type { GenerateRequest, LoadRequest, WorkerEvent, WorkerRequest } from '../protocol';

const post = (event: WorkerEvent) => (self as unknown as Worker).postMessage(event);

let gpu: Promise<WebmlDevice> | null = null;
let loaded: { model: string; contextLength?: number; session: GgufSession } | null = null;
let loading: Promise<void> | null = null;
let generating: string | null = null;
/** The running generation, so unloading can wait for it instead of freeing its buffers. */
let running: Promise<void> | null = null;
let interrupted = false;

/** The device, created on first use and again after a loss. */
function device(): Promise<WebmlDevice> {
  gpu ??= requestWebmlDevice().then((d) => {
    void d.device.lost.then((info) => {
      gpu = null;
      loaded = null;
      if (info.reason !== 'destroyed') {
        post({
          type: 'error',
          message: `the GPU device was lost (${info.message || info.reason}); load the model again`,
        });
      }
    });
    return d;
  });
  return gpu;
}

async function load(req: LoadRequest): Promise<void> {
  if (loaded && loaded.model === req.model && loaded.contextLength === req.contextLength) {
    post({
      type: 'ready',
      model: req.model,
      dtype: req.dtype,
      device: 'webgpu',
      loadMs: 0,
      engine: 'gguf',
      quant: loaded.session.quant,
      contextLength: loaded.session.runtime.context,
    });
    return;
  }
  await unload(false);
  if (req.device !== 'webgpu') throw new Error('GGUF models run on WebGPU only');
  const ref = parseGgufModelId(req.model);
  if (!ref) throw new Error(`not a GGUF model id: ${req.model}`);
  const started = performance.now();

  let file = await openGguf(req.model);
  if (!file) {
    // Check the header over Range requests before committing to the download.
    const url = hfResolveUrl(ref.repo, ref.file);
    const remote = new HttpSource(url);
    const check = preflight(await readGgufHeader(remote));
    if (!check.ok) throw new Error(`cannot run this model: ${check.reasons.join('; ')}`);
    let lastSent = 0;
    file = await downloadGguf(req.model, url, {
      size: remote.size ?? undefined,
      onProgress: (n, total) => {
        const now = performance.now();
        if (now - lastSent < 100) return;
        lastSent = now;
        post({
          type: 'progress',
          status: 'progress',
          file: ref.file,
          loaded: n,
          total: total ?? 0,
        });
      },
    });
    post({ type: 'progress', status: 'done', file: ref.file, loaded: file.size, total: file.size });
  }

  // Uploading weights and compiling pipelines: the client shows this as warm-up.
  post({ type: 'progress', status: 'warmup', file: 'gpu', loaded: 0, total: 0 });
  const session = await GgufSession.open(await device(), blobSource(file), {
    contextLength: req.contextLength,
  });
  loaded = { model: req.model, contextLength: req.contextLength, session };
  post({
    type: 'ready',
    model: req.model,
    dtype: req.dtype,
    device: 'webgpu',
    loadMs: Math.round(performance.now() - started),
    engine: 'gguf',
    quant: session.quant,
    contextLength: session.runtime.context,
  });
}

/** Stop any generation, wait for it to return, then free the model's GPU memory. */
async function unload(announce = true): Promise<void> {
  interrupted = true;
  await running?.catch(() => undefined);
  loaded?.session.destroy();
  loaded = null;
  if (announce) post({ type: 'unloaded' });
}

async function generate(req: GenerateRequest): Promise<void> {
  if (!loaded) throw new Error('no model loaded');
  interrupted = false;
  const result = await loaded.session.generate(
    req,
    {
      onDelta: (text) => post({ type: 'delta', id: req.id, text }),
      onStep: (step) => post({ type: 'step', id: req.id, ...step }),
    },
    () => interrupted,
  );
  post({ type: 'done', id: req.id, ...result });
}

function describe(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

self.addEventListener('message', (event: MessageEvent<WorkerRequest>) => {
  const req = event.data;
  switch (req.type) {
    case 'load':
      loading = (loading ?? Promise.resolve())
        .then(() => load(req))
        .catch((err) => post({ type: 'error', message: describe(err) }))
        .finally(() => (loading = null));
      return;
    case 'unload':
      void unload().catch((err) => post({ type: 'error', message: describe(err) }));
      return;
    case 'interrupt':
      interrupted = true;
      return;
    case 'generate':
      if (generating) {
        post({ type: 'error', id: req.id, message: 'the model is busy with another reply' });
        return;
      }
      generating = req.id;
      running = (loading ?? Promise.resolve())
        .then(() => generate(req))
        .catch((err) => post({ type: 'error', id: req.id, message: describe(err) }))
        .finally(() => {
          generating = null;
          running = null;
        });
      return;
  }
});
