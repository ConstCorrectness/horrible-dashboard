/** WebmlEngine's routing between the two workers, with fake workers. */
import { describe, expect, it } from 'vitest';

import { WebmlEngine, type WebmlEngineOptions } from '../client';
import { nodeGgufModelId, parseGgufModelId, parseNodeGgufModelId } from '../gguf/store';
import type { EngineKind, WorkerEvent, WorkerRequest } from '../protocol';

class FakeWorker extends EventTarget {
  sent: WorkerRequest[] = [];
  terminated = false;
  constructor(readonly kind: EngineKind) {
    super();
  }
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

function setup(options: WebmlEngineOptions = {}) {
  const workers: FakeWorker[] = [];
  const engine = new WebmlEngine((kind) => {
    const w = new FakeWorker(kind);
    workers.push(w);
    return w as unknown as Worker;
  }, options);
  return { workers, engine };
}

const GGUF = 'gguf:org/repo/model-Q8_0.gguf';

describe('WebmlEngine routing', () => {
  it('starts the GGUF worker for a gguf: id and passes the context length', async () => {
    const { workers, engine } = setup();
    const loading = engine.load(GGUF, 'q4', 'webgpu', { contextLength: 2048 });
    expect(workers.map((w) => w.kind)).toEqual(['gguf']);
    expect(workers[0].sent[0]).toEqual({
      type: 'load',
      model: GGUF,
      dtype: 'q4',
      device: 'webgpu',
      contextLength: 2048,
    });
    workers[0].emit({
      type: 'ready',
      model: GGUF,
      dtype: 'q4',
      device: 'webgpu',
      loadMs: 5,
      engine: 'gguf',
      quant: 'Q8_0',
      contextLength: 2048,
    });
    await loading;
    expect(engine.getState()).toMatchObject({ kind: 'ready', engine: 'gguf', quant: 'Q8_0' });
  });

  it('replaces the worker when the next model needs the other engine', async () => {
    const { workers, engine } = setup();
    const first = engine.load('org/onnx-model', 'q4f16');
    workers[0].emit({
      type: 'ready',
      model: 'org/onnx-model',
      dtype: 'q4f16',
      device: 'webgpu',
      loadMs: 1,
    });
    await first;
    expect(engine.getState()).toMatchObject({ engine: 'onnx' });

    const reply = engine.generate({ messages: [{ role: 'user', content: 'hi' }] });
    void engine.load(GGUF, 'q4');
    await expect(reply).rejects.toThrow(/superseded by loading another model/);
    expect(workers.map((w) => [w.kind, w.terminated])).toEqual([
      ['onnx', true],
      ['gguf', false],
    ]);
    expect(workers[1].sent[0]).toMatchObject({ type: 'load', model: GGUF });
  });

  it('keeps one worker across models of the same engine', () => {
    const { workers, engine } = setup();
    const first = engine.load(GGUF, 'q4');
    void engine.load('gguf:org/repo/other-Q4_0.gguf', 'q4');
    void first.catch(() => undefined); // superseded by the second load
    expect(workers).toHaveLength(1);
    expect(workers[0].sent.filter((r) => r.type === 'load')).toHaveLength(2);
  });
});

describe('node GGUFs (gguf-node:)', () => {
  const PATH = 'D:\\models\\trained\\tiny-Q8_0.gguf';
  const NODE = nodeGgufModelId(PATH);

  it('round-trips a path, Windows or not, and is not a Hub id', () => {
    expect(parseNodeGgufModelId(NODE)).toEqual({ path: PATH });
    // Ollama's blobs have no extension.
    expect(parseNodeGgufModelId(nodeGgufModelId('/x/blobs/sha256-ab12'))).toEqual({
      path: '/x/blobs/sha256-ab12',
    });
    expect(parseNodeGgufModelId('gguf-node:')).toBeNull();
    expect(parseNodeGgufModelId(GGUF)).toBeNull();
    expect(parseGgufModelId(NODE)).toBeNull();
  });

  it('runs on the GGUF worker, with the URL the window resolved', () => {
    const asked: string[] = [];
    const { workers, engine } = setup({
      sourceUrl: (model) => {
        asked.push(model);
        return parseNodeGgufModelId(model) ? '/api/llamacpp/models/file?path=x' : null;
      },
    });
    void engine.load(NODE, 'q4f16');
    expect(workers.map((w) => w.kind)).toEqual(['gguf']);
    expect(workers[0].sent[0]).toMatchObject({
      type: 'load',
      model: NODE,
      url: '/api/llamacpp/models/file?path=x',
    });
    expect(asked).toEqual([NODE]);
  });
});
