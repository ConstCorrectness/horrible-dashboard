/**
 * The path the app takes: `WebmlEngine` (the client the playground, the `browser`
 * chat provider and `{webllm}` share) routing a `gguf:` id to gguf.worker.ts, which
 * loads the file from OPFS and streams a reply over the protocol.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { listCachedModels } from '../src/cache';
import { WebmlEngine, engineFor } from '../src/client';
import { deleteGguf, downloadGguf, nodeGgufModelId, parseNodeGgufModelId } from '../src/gguf/store';
import f32Expected from './fixtures/tiny-llama-f32.expected.json';
import f32Url from './fixtures/tiny-llama-f32.gguf?url';
import { fileServer, fixtureBytes } from './harness';

const ID = 'gguf:test/tiny/tiny-llama-f32.gguf';
const text = (ids: number[]) => ids.map((i) => `t${i}`).join('');

describe('the GGUF worker through WebmlEngine', () => {
  const engine = new WebmlEngine();

  beforeAll(async () => {
    // Seed OPFS as a finished download would, so the worker never goes to the Hub.
    await deleteGguf(ID);
    await downloadGguf(ID, 'https://hub/x', {
      fetch: fileServer(await fixtureBytes(f32Url)).fetcher,
    });
  });

  afterAll(async () => {
    engine.terminate();
    await deleteGguf(ID);
  });

  it('routes gguf: ids to the GGUF engine', () => {
    expect(engineFor(ID)).toBe('gguf');
    expect(engineFor('onnx-community/Qwen3-0.6B-ONNX')).toBe('onnx');
  });

  it('lists the stored file as a runnable model', async () => {
    const models = await listCachedModels();
    expect(models.find((m) => m.id === ID)).toMatchObject({ id: ID, files: 1 });
    expect(models.find((m) => m.id === ID)?.partial).toBeUndefined();
  });

  it("loads, reports the engine and quant, and streams llama.cpp's continuation", async () => {
    await engine.load(ID, 'q4');
    expect(engine.getState()).toMatchObject({
      kind: 'ready',
      model: ID,
      engine: 'gguf',
      quant: 'F32',
      contextLength: 64,
      device: 'webgpu',
    });

    let streamed = '';
    const steps: number[] = [];
    const result = await engine.generate(
      {
        messages: [{ role: 'user', content: text(f32Expected.prompt) }],
        temperature: 0,
        maxNewTokens: f32Expected.generated.length,
        topK: 2,
      },
      { onDelta: (d) => (streamed += d), onStep: (s) => steps.push(s.index) },
    );
    expect(result.text).toBe(text(f32Expected.generated));
    expect(streamed).toBe(result.text);
    expect(steps).toHaveLength(f32Expected.generated.length);
    expect(result.stop).toBe('length');
    expect(result.tokensPerSecond).toBeGreaterThan(0);
  });

  it('stops on abort, and frees the model on unload', async () => {
    const ctrl = new AbortController();
    let deltas = 0;
    const pending = engine.generate(
      {
        messages: [{ role: 'user', content: text(f32Expected.prompt) }],
        temperature: 0.8,
        maxNewTokens: 50,
      },
      {
        signal: ctrl.signal,
        onDelta: () => {
          if (++deltas === 3) ctrl.abort();
        },
      },
    );
    const result = await pending;
    expect(result.stop).toBe('interrupt');

    engine.unload();
    await expect.poll(() => engine.getState().kind).toBe('idle');
  });

  it('refuses a model id that is not in the store and not a Hub file', async () => {
    await expect(engine.load('gguf:bad', 'q4')).rejects.toThrow(/not a GGUF model id/);
  });
});

/**
 * 6.5: a GGUF this node already has. The window resolves where its bytes are (the
 * node's `/api/llamacpp/models/file`; here the test server, which honours `Range`
 * the same way) and the worker reads it from there, storing nothing in OPFS.
 */
describe('a node GGUF through WebmlEngine', () => {
  const NODE_PATH = 'C:\\models\\trained\\tiny-llama-f32.gguf';
  const NODE_ID = nodeGgufModelId(NODE_PATH);
  const urls: string[] = [];
  const engine = new WebmlEngine(undefined, {
    sourceUrl: (model) => {
      urls.push(model);
      return parseNodeGgufModelId(model) ? new URL(f32Url, location.href).href : null;
    },
  });

  afterAll(() => engine.terminate());

  it("routes gguf-node: ids to the GGUF engine and streams llama.cpp's continuation", async () => {
    expect(engineFor(NODE_ID)).toBe('gguf');
    expect(parseNodeGgufModelId(NODE_ID)?.path).toBe(NODE_PATH);

    await engine.load(NODE_ID, 'q4');
    expect(urls).toEqual([NODE_ID]);
    expect(engine.getState()).toMatchObject({ kind: 'ready', model: NODE_ID, quant: 'F32' });

    const result = await engine.generate({
      messages: [{ role: 'user', content: text(f32Expected.prompt) }],
      temperature: 0,
      maxNewTokens: f32Expected.generated.length,
    });
    expect(result.text).toBe(text(f32Expected.generated));
    // Read from the node, never copied into OPFS.
    expect((await listCachedModels()).some((m) => m.id === NODE_ID)).toBe(false);
  });

  it('refuses a node model when the window gave no address for it', async () => {
    const bare = new WebmlEngine();
    try {
      await expect(bare.load(NODE_ID, 'q4')).rejects.toThrow(/no address/);
    } finally {
      bare.terminate();
    }
  });
});
