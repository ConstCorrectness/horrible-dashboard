import { describe, expect, it } from 'vitest';

import { deleteCachedModel, listCachedModels, modelIdFromUrl } from '../cache';
import { CATALOG, formatBytes, pickDtype } from '../catalog';
import { WebmlEngine, loadTotals } from '../client';
import { distribution, topK } from '../distribution';
import type { WorkerEvent, WorkerRequest } from '../protocol';

describe('distribution', () => {
  it('softmaxes, ranks and measures entropy', () => {
    const d = distribution([0, 0, 0, 0], 2);
    expect([...d.probs]).toEqual([0.25, 0.25, 0.25, 0.25]);
    expect(d.entropy).toBeCloseTo(2, 6);
    expect(d.top).toHaveLength(2);

    const peaked = distribution([10, 0, -Infinity, 1], 3);
    expect(peaked.top.map((t) => t.id)).toEqual([0, 3, 1]);
    expect(peaked.probs[2]).toBe(0);
    expect(peaked.probs.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 6);
    expect(peaked.entropy).toBeLessThan(0.01 + -Math.log2(peaked.top[0].p) * 1);
  });

  it('survives an all-masked row', () => {
    const d = distribution([-Infinity, -Infinity], 3);
    expect(d.top).toEqual([]);
    expect(d.entropy).toBe(0);
  });

  it('topK keeps the k largest, descending', () => {
    const values = [3, 9, 1, 7, 9.5, 0, 8];
    expect(topK(values, 3).map((t) => t.id)).toEqual([4, 1, 6]);
    expect(topK(values, 0)).toEqual([]);
    expect(topK([1, 2], 5).map((t) => t.id)).toEqual([1, 0]);
  });

  it('reuses the buffer it is given', () => {
    const buf = new Float32Array(3);
    expect(distribution([1, 2, 3], 1, buf).probs).toBe(buf);
  });
});

describe('catalog', () => {
  it('has unique ids, sizes and a preferred variant', () => {
    expect(new Set(CATALOG.map((m) => m.id)).size).toBe(CATALOG.length);
    for (const m of CATALOG) expect(Object.keys(m.sizes).length).toBeGreaterThan(0);
  });

  it('falls back from q4f16 when the GPU has no shader-f16', () => {
    const qwen = CATALOG.find((m) => m.id.includes('Qwen3-0.6B'))!;
    expect(pickDtype(qwen, true)).toBe('q4f16');
    expect(pickDtype(qwen, false)).toBe('q4');
    expect(pickDtype(undefined, false)).toBe('q4');
    expect(pickDtype(qwen, false, 'fp16')).toBe('fp16');
  });

  it('formats bytes', () => {
    expect(formatBytes(569_789_750)).toBe('570 MB');
    expect(formatBytes(1_426_069_098)).toBe('1.4 GB');
    expect(formatBytes(0)).toBe('0 B');
  });
});

/** Just enough CacheStorage for the cache module. */
function fakeStorage(entries: Record<string, number | null>): CacheStorage {
  const map = new Map(Object.entries(entries));
  const cache = {
    keys: async () => [...map.keys()].map((url) => ({ url }) as Request),
    match: async (req: Request) => {
      const size = map.get(req.url);
      return new Response('', { headers: size == null ? {} : { 'content-length': String(size) } });
    },
    delete: async (req: Request) => map.delete(req.url),
  };
  return {
    has: async () => true,
    open: async () => cache as unknown as Cache,
  } as unknown as CacheStorage;
}

describe('cache', () => {
  it('parses model ids out of Hub URLs', () => {
    expect(
      modelIdFromUrl(
        'https://huggingface.co/onnx-community/Qwen3-0.6B-ONNX/resolve/main/onnx/model_q4f16.onnx',
      ),
    ).toBe('onnx-community/Qwen3-0.6B-ONNX');
    expect(
      modelIdFromUrl('https://cdn.jsdelivr.net/npm/onnxruntime-web@1/dist/ort.wasm'),
    ).toBeNull();
    expect(modelIdFromUrl('not a url')).toBeNull();
  });

  it('groups files into models and deletes one', async () => {
    const store = fakeStorage({
      'https://huggingface.co/a/b/resolve/main/config.json': 100,
      'https://huggingface.co/a/b/resolve/main/onnx/model.onnx': 5000,
      'https://huggingface.co/c/d/resolve/main/tokenizer.json': null,
      'https://cdn.jsdelivr.net/npm/onnxruntime-web@1/dist/ort.wasm': 999,
    });
    expect(await listCachedModels(store)).toEqual([
      { id: 'a/b', files: 2, bytes: 5100 },
      { id: 'c/d', files: 1, bytes: 0 },
    ]);
    expect(await deleteCachedModel('a/b', store)).toBe(2);
    expect((await listCachedModels(store)).map((m) => m.id)).toEqual(['c/d']);
    expect(await listCachedModels(null)).toEqual([]);
  });
});

/** A worker double: records what the engine sends and lets the test answer. */
class FakeWorker extends EventTarget {
  sent: WorkerRequest[] = [];
  terminated = false;
  postMessage(req: WorkerRequest) {
    this.sent.push(req);
  }
  terminate() {
    this.terminated = true;
  }
  emit(event: WorkerEvent) {
    this.dispatchEvent(new MessageEvent('message', { data: event }));
  }
}

describe('WebmlEngine', () => {
  function setup() {
    const worker = new FakeWorker();
    const engine = new WebmlEngine(() => worker as unknown as Worker);
    return { worker, engine };
  }

  it('creates the worker lazily and tracks a load to ready', async () => {
    const { worker, engine } = setup();
    expect(worker.sent).toEqual([]);
    const loading = engine.load('a/b', 'q4f16');
    expect(worker.sent[0]).toEqual({
      type: 'load',
      model: 'a/b',
      dtype: 'q4f16',
      device: 'webgpu',
    });
    worker.emit({
      type: 'progress',
      status: 'progress',
      file: 'model.onnx',
      loaded: 50,
      total: 100,
    });
    const s = engine.getState();
    expect(s.kind === 'loading' && loadTotals(s.files)).toEqual({ loaded: 50, total: 100 });
    worker.emit({ type: 'progress', status: 'warmup', file: 'shaders', loaded: 0, total: 0 });
    expect(engine.getState()).toMatchObject({ kind: 'loading', phase: 'warmup' });
    worker.emit({ type: 'ready', model: 'a/b', dtype: 'q4f16', device: 'webgpu', loadMs: 12 });
    await loading;
    expect(engine.getState()).toMatchObject({ kind: 'ready', model: 'a/b' });
    // Same model again: nothing sent.
    await engine.load('a/b', 'q4f16');
    expect(worker.sent).toHaveLength(1);
  });

  it('rejects a load that errors and records the error state', async () => {
    const { worker, engine } = setup();
    const loading = engine.load('x/y', 'q4');
    worker.emit({ type: 'error', message: 'Could not locate file' });
    await expect(loading).rejects.toThrow('Could not locate file');
    expect(engine.getState()).toEqual({
      kind: 'error',
      message: 'Could not locate file',
      model: 'x/y',
    });
  });

  it('streams a generation and refuses a second one meanwhile', async () => {
    const { worker, engine } = setup();
    const loading = engine.load('a/b', 'q4');
    worker.emit({ type: 'ready', model: 'a/b', dtype: 'q4', device: 'webgpu', loadMs: 1 });
    await loading;

    const deltas: string[] = [];
    const steps: number[] = [];
    const reply = engine.generate(
      { messages: [{ role: 'user', content: 'hi' }], topK: 3 },
      { onDelta: (t) => deltas.push(t), onStep: (s) => steps.push(s.index) },
    );
    const req = worker.sent.at(-1) as Extract<WorkerRequest, { type: 'generate' }>;
    expect(req).toMatchObject({ type: 'generate', topK: 3 });
    expect(engine.busy).toBe(true);
    await expect(engine.generate({ messages: [] })).rejects.toThrow('busy');

    worker.emit({ type: 'delta', id: req.id, text: 'Hel' });
    worker.emit({ type: 'step', id: req.id, index: 0, token: 'Hel', p: 0.5, entropy: 1, topk: [] });
    worker.emit({ type: 'delta', id: 'other', text: 'ignored' });
    worker.emit({
      type: 'done',
      id: req.id,
      text: 'Hello',
      stop: 'eos',
      usage: { promptTokens: 5, completionTokens: 2 },
      ttftMs: 40,
      tokensPerSecond: 30,
    });
    await expect(reply).resolves.toMatchObject({ text: 'Hello', stop: 'eos', tokensPerSecond: 30 });
    expect(deltas).toEqual(['Hel']);
    expect(steps).toEqual([0]);
    expect(engine.busy).toBe(false);
  });

  it('interrupts on abort and fails pending work when the worker crashes', async () => {
    const { worker, engine } = setup();
    const loading = engine.load('a/b', 'q4');
    worker.emit({ type: 'ready', model: 'a/b', dtype: 'q4', device: 'webgpu', loadMs: 1 });
    await loading;
    const ctrl = new AbortController();
    const reply = engine.generate({ messages: [] }, { signal: ctrl.signal });
    ctrl.abort();
    expect(worker.sent.at(-1)).toEqual({ type: 'interrupt' });
    worker.dispatchEvent(Object.assign(new Event('error'), { message: 'device lost' }));
    await expect(reply).rejects.toThrow('device lost');
    expect(worker.terminated).toBe(true);
    expect(engine.getState()).toEqual({ kind: 'error', message: 'device lost' });
  });
});
