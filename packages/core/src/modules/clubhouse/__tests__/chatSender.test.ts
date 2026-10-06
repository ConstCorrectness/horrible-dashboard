import { describe, expect, it } from 'vitest';

import { ChatSender } from '../chatSender';

class Err extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** A sender on a fake clock: `sleep` advances time instead of waiting. */
function rig(script: (call: number, text: string) => void | Promise<void> = () => undefined) {
  let t = 1_000_000;
  const sent: { text: string; at: number }[] = [];
  let calls = 0;
  const sender = new ChatSender(
    async (_channel, text) => {
      calls++;
      await script(calls, text);
      sent.push({ text, at: t });
    },
    {
      minGapMs: 3000,
      retryDelaysMs: [5000, 10000],
      now: () => t,
      sleep: async (ms) => {
        t += ms;
      },
    },
  );
  return { sender, sent, advance: (ms: number) => (t += ms), calls: () => calls };
}

describe('spacing', () => {
  it('holds a minimum gap between messages, across callers', async () => {
    const { sender, sent } = rig();
    await Promise.all([
      sender.post('c', 'one'),
      sender.post('c', 'two'),
      sender.post('c', 'three'),
    ]);
    expect(sent.map((s) => s.text)).toEqual(['one', 'two', 'three']);
    expect(sent[1].at - sent[0].at).toBeGreaterThanOrEqual(3000);
    expect(sent[2].at - sent[1].at).toBeGreaterThanOrEqual(3000);
  });

  it('does not delay the first message', async () => {
    const { sender, sent } = rig();
    const before = 1_000_000;
    await sender.post('c', 'hi');
    expect(sent[0].at).toBe(before);
  });
});

describe('a 429', () => {
  it('is retried after a wait, and the message still goes out', async () => {
    const { sender, sent, calls } = rig((n) => {
      if (n === 1) throw new Err('Less is more! Please wait a bit', 429);
    });
    await expect(sender.post('c', 'hello')).resolves.toBe('sent');
    expect(calls()).toBe(2);
    expect(sent).toHaveLength(1);
  });

  it('gives up after the retries are spent and rejects with the original error', async () => {
    const { sender, calls } = rig(() => {
      throw new Err('Less is more! Please wait a bit', 429);
    });
    await expect(sender.post('c', 'hello')).rejects.toThrow('Less is more');
    // First try plus two retries.
    expect(calls()).toBe(3);
  });

  it('does not wedge the messages queued behind a failure', async () => {
    const { sender, sent } = rig((n, text) => {
      if (text === 'bad') throw new Err('nope', 500);
      void n;
    });
    const results = await Promise.allSettled([sender.post('c', 'bad'), sender.post('c', 'good')]);
    expect(results[0].status).toBe('rejected');
    expect(results[1]).toMatchObject({ status: 'fulfilled', value: 'sent' });
    expect(sent.map((s) => s.text)).toEqual(['good']);
  });

  it('does not retry an error that waiting cannot fix', async () => {
    const { sender, calls } = rig(() => {
      throw new Err('cannot send message', 400);
    });
    await expect(sender.post('c', 'x')).rejects.toThrow('cannot send message');
    expect(calls()).toBe(1);
  });
});

describe('repeats', () => {
  it('skips an agent line it already posted, instead of being refused for it', async () => {
    const { sender, calls } = rig();
    await sender.post('c', 'Anyone want a hot take?', { dedupe: true });
    await expect(sender.post('c', 'Anyone want a hot take?', { dedupe: true })).resolves.toBe(
      'duplicate',
    );
    expect(calls()).toBe(1);
  });

  it('never second-guesses a person typing the same thing again', async () => {
    const { sender, calls } = rig();
    await sender.post('c', 'gm');
    await expect(sender.post('c', 'gm')).resolves.toBe('sent');
    expect(calls()).toBe(2);
  });

  it('forgets a line after the window, and keeps rooms apart', async () => {
    const { sender, advance } = rig();
    await sender.post('a', 'same', { dedupe: true });
    expect(sender.wasPosted('a', 'same')).toBe(true);
    expect(sender.wasPosted('b', 'same')).toBe(false);
    advance(11 * 60_000);
    expect(sender.wasPosted('a', 'same')).toBe(false);
  });

  it('learns from Clubhouse’s own "said already" refusal', async () => {
    const { sender, calls } = rig(() => {
      throw new Err('Looks like that’s been said already!', 400);
    });
    await expect(sender.post('c', 'hello', { dedupe: true })).rejects.toThrow('said already');
    expect(calls()).toBe(1); // not retried
    await expect(sender.post('c', 'hello', { dedupe: true })).resolves.toBe('duplicate');
  });
});

describe('leaving a room', () => {
  it('drops what is still queued instead of posting it to the room just left', async () => {
    const { sender, sent } = rig();
    const first = sender.post('old', 'one');
    const second = sender.post('old', 'two');
    await first;
    sender.reset();
    await expect(second).resolves.toBe('dropped');
    expect(sent.map((s) => s.text)).toEqual(['one']);
  });
});
