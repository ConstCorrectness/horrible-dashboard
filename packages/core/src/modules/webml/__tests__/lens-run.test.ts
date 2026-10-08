import { afterEach, describe, expect, it } from 'vitest';

import type { LayerLens, WorkerEvent, WorkerRequest } from '@horrible/webml';

import { AppEngine } from '../engine';
import { LENS_RUN_TOKENS, lensRuns, settlesAt, type LensToken } from '../lens-run';

/** A layer whose best token is `best`. */
const layer = (best: string, p = 0.5): LayerLens => ({
  norm: 10,
  entropy: 1,
  top: [{ token: best, p }],
});

const token = (chosen: string, bests: string[]): LensToken => ({
  token: chosen,
  p: 0.9,
  layers: bests.map((b) => layer(b)),
});

afterEach(() => lensRuns.reset());

describe('settlesAt', () => {
  it('is the first layer from which the chosen token leads to the end', () => {
    // Leads at layer 1, loses it at 2, and holds from 3.
    expect(settlesAt(token('Paris', ['no', 'Paris', 'France', 'Paris', 'Paris']))).toBe(3);
    expect(settlesAt(token('a', ['a', 'a']))).toBe(0);
  });

  it('is null when the last layer prefers another token (a sampled runner-up)', () => {
    expect(settlesAt(token('b', ['b', 'a']))).toBeNull();
  });
});

describe('lensRuns', () => {
  it('collects a run as it streams, skipping steps without a lens', () => {
    lensRuns.start('gguf:o/r/m.gguf');
    lensRuns.push({ token: 'x', p: 0.4 });
    lensRuns.push({ token: 'y', p: 0.6, layers: [layer('y'), layer('y')] });
    expect(lensRuns.get()).toMatchObject({ model: 'gguf:o/r/m.gguf', layers: 2, live: true });
    expect(lensRuns.get()!.tokens.map((t) => t.token)).toEqual(['y']);
    lensRuns.finish();
    expect(lensRuns.get()!.live).toBe(false);
    // A finished run takes no more steps.
    lensRuns.push({ token: 'z', p: 1, layers: [layer('z')] });
    expect(lensRuns.get()!.tokens).toHaveLength(1);
  });

  it('keeps a long reply to its last tokens', () => {
    lensRuns.start('m');
    for (let i = 0; i < LENS_RUN_TOKENS + 3; i++)
      lensRuns.push({ token: `t${i}`, p: 1, layers: [layer('a')] });
    const tokens = lensRuns.get()!.tokens;
    expect(tokens).toHaveLength(LENS_RUN_TOKENS);
    expect(tokens[0].token).toBe('t3');
  });
});

class FakeWorker extends EventTarget {
  sent: WorkerRequest[] = [];
  postMessage(req: WorkerRequest) {
    this.sent.push(req);
  }
  terminate() {}
  emit(event: WorkerEvent) {
    this.dispatchEvent(new MessageEvent('message', { data: event }));
  }
}

describe('the app engine', () => {
  async function ready() {
    const worker = new FakeWorker();
    const engine = new AppEngine(() => worker as unknown as Worker);
    const loading = engine.load('gguf:o/r/m.gguf', 'q4');
    worker.emit({
      type: 'ready',
      model: 'gguf:o/r/m.gguf',
      dtype: 'q4',
      device: 'webgpu',
      loadMs: 1,
      engine: 'gguf',
    });
    await loading;
    return { worker, engine };
  }

  const done = (id: string): WorkerEvent => ({
    type: 'done',
    id,
    text: 'Paris',
    stop: 'eos',
    usage: { promptTokens: 3, completionTokens: 1 },
    ttftMs: 1,
    tokensPerSecond: 1,
  });

  it('records a lens generation for the explorer while still passing steps on', async () => {
    const { worker, engine } = await ready();
    const seen: string[] = [];
    const reply = engine.generate(
      { messages: [{ role: 'user', content: 'capital?' }], topK: 5, lens: true },
      { onStep: (s) => seen.push(s.token) },
    );
    const id = (worker.sent.at(-1) as { id: string }).id;
    worker.emit({
      type: 'step',
      id,
      index: 0,
      token: 'Paris',
      p: 0.9,
      entropy: 0.5,
      topk: [],
      layers: [layer('no'), layer('Paris')],
    });
    // Live: the explorer sees the token before the reply ends.
    expect(lensRuns.get()).toMatchObject({ model: 'gguf:o/r/m.gguf', live: true, layers: 2 });
    worker.emit(done(id));
    await reply;
    expect(seen).toEqual(['Paris']);
    expect(lensRuns.get()!.live).toBe(false);
    expect(lensRuns.get()!.tokens.map((t) => settlesAt(t))).toEqual([1]);
  });

  it('leaves the last run alone for a generation without the lens', async () => {
    const { worker, engine } = await ready();
    lensRuns.start('earlier');
    lensRuns.finish();
    const before = lensRuns.get();
    const reply = engine.generate({ messages: [{ role: 'user', content: 'hi' }], topK: 5 });
    worker.emit(done((worker.sent.at(-1) as { id: string }).id));
    await reply;
    expect(lensRuns.get()).toBe(before);
  });
});
