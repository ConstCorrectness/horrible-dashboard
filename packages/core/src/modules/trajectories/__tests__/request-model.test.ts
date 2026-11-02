import { describe, expect, it } from 'vitest';

import type { RequestDetail, RequestMessage } from '../../interpretability/requests';
import {
  answersIn,
  buildMinimap,
  contentPieces,
  messageFamily,
  newIndexes,
  pairToolCalls,
  partnersOf,
  popoverFor,
  requestLabel,
  resolveFocus,
  templateGap,
} from '../panels/request-model';

function msg(
  index: number,
  body: Record<string, unknown>,
  over: Partial<RequestMessage> = {},
): RequestMessage {
  return {
    index,
    role: String(body.role),
    hash: `h${index}`,
    body,
    tokens: 10,
    chars: typeof body.content === 'string' ? body.content.length : 0,
    segments: [],
    fromRole: null,
    ...over,
  };
}

function detail(messages: RequestMessage[], over: Partial<RequestDetail> = {}): RequestDetail {
  return {
    id: 'r1',
    turnId: 't',
    round: 1,
    attempt: 0,
    ts: 0,
    dialect: 'openai',
    provider: 'lmstudio',
    model: 'm',
    status: 'ok',
    messageCount: messages.length,
    estTokens: 0,
    exact: false,
    promptTokens: null,
    completionTokens: null,
    toolCalls: 0,
    toolNames: [],
    responsePreview: '',
    durationMs: null,
    error: null,
    reconstructed: false,
    messages,
    tools: [{ type: 'function', function: { name: 'files.read' } }],
    toolsHash: 'tools',
    toolsTokens: 30,
    params: {},
    response: null,
    contextLength: null,
    notes: [],
    ...over,
  };
}

const call = (id: string, name: string) => ({
  id,
  type: 'function',
  function: { name, arguments: '{}' },
});

describe('requestLabel', () => {
  it('names a second call in one round as the retry', () => {
    expect(requestLabel({ round: 2, attempt: 0 })).toBe('R2');
    expect(requestLabel({ round: 2, attempt: 1 })).toBe('R2 · retry');
    expect(requestLabel({ round: 2, attempt: 3 })).toBe('R2 · retry 3');
  });
});

describe('pairToolCalls', () => {
  it('pairs by tool_call_id, out of order', () => {
    const messages = [
      msg(0, { role: 'user', content: 'q' }),
      msg(1, { role: 'assistant', content: '', tool_calls: [call('a', 'x'), call('b', 'y')] }),
      msg(2, { role: 'tool', content: 'B', tool_call_id: 'b' }),
      msg(3, { role: 'tool', content: 'A', tool_call_id: 'a' }),
    ];
    const p = pairToolCalls(messages);
    expect(p.callFor.get(2)).toMatchObject({ message: 1, id: 'b', name: 'y' });
    expect(p.callFor.get(3)).toMatchObject({ message: 1, id: 'a', name: 'x' });
    expect(p.resultsFor.get(1)).toEqual([2, 3]);
    expect(partnersOf(3, p)).toEqual([1]);
    expect(partnersOf(1, p)).toEqual([2, 3]);
  });

  it('pairs Ollama results by tool_name, then order, when there are no ids', () => {
    const messages = [
      msg(0, {
        role: 'assistant',
        content: '',
        tool_calls: [{ function: { name: 'x' } }, { function: { name: 'y' } }],
      }),
      msg(1, { role: 'tool', content: 'Y', tool_name: 'y' }),
      msg(2, { role: 'tool', content: 'X?' }),
    ];
    const p = pairToolCalls(messages);
    expect(p.callFor.get(1)?.name).toBe('y');
    // No name: the first call still open.
    expect(p.callFor.get(2)?.name).toBe('x');
  });

  it('leaves a result with nothing to answer unpaired', () => {
    const p = pairToolCalls([msg(0, { role: 'tool', content: 'orphan' })]);
    expect(p.callFor.size).toBe(0);
  });
});

describe('newIndexes', () => {
  it('is null with no previous request to compare against', () => {
    expect(newIndexes(undefined, [msg(0, { role: 'user', content: 'q' })])).toBeNull();
  });

  it('marks only what was appended, multiset-aware', () => {
    const prev = [msg(0, { role: 'user' }), msg(1, { role: 'tool' }, { hash: 'same' })];
    const cur = [
      ...prev,
      msg(2, { role: 'tool' }, { hash: 'same' }),
      msg(3, { role: 'assistant' }),
    ];
    expect([...(newIndexes(prev, cur) ?? [])]).toEqual([2, 3]);
  });
});

describe('buildMinimap', () => {
  const messages = [
    msg(0, { role: 'system', content: 's' }, { tokens: 400 }),
    msg(1, { role: 'user', content: 'q' }, { tokens: 0 }),
  ];

  it('draws tools first, then messages, then the unused window', () => {
    const map = buildMinimap(detail(messages, { contextLength: 1000 }), null);
    expect(map.items.map((i) => i.key)).toEqual(['tools', 'm0', 'm1', 'free']);
    expect(map.used).toBe(430);
    expect(map.items.at(-1)).toMatchObject({ family: 'free', grow: 570 });
  });

  it('never gives a message zero weight, and omits the window when unknown', () => {
    const map = buildMinimap(detail(messages), null);
    expect(map.items.find((i) => i.key === 'm1')?.grow).toBe(1);
    expect(map.items.some((i) => i.key === 'free')).toBe(false);
    expect(map.window).toBeNull();
  });

  it('carries new-ness onto the cells', () => {
    const map = buildMinimap(detail(messages), new Set([1]));
    expect(map.items.find((i) => i.key === 'm1')?.isNew).toBe(true);
    expect(map.items.find((i) => i.key === 'm0')?.isNew).toBe(false);
  });
});

describe('provenance', () => {
  const joined = msg(
    0,
    { role: 'system', content: 'AAA\n\nBB' },
    {
      segments: [
        {
          src: 0,
          start: 0,
          end: 3,
          kind: 'system',
          label: 'System prompt',
          toolName: null,
          clipped: false,
        },
        {
          src: 1,
          start: 5,
          end: 7,
          kind: 'skills',
          label: 'Skill catalog',
          toolName: null,
          clipped: true,
        },
      ],
    },
  );

  it('splits a joined message into its parts and the seam between them', () => {
    const pieces = contentPieces('AAA\n\nBB', joined.segments);
    expect(pieces.map((p) => [p.text, p.family])).toEqual([
      ['AAA', 'system'],
      ['\n\n', null],
      ['BB', 'skills'],
    ]);
    expect(pieces[2].clipped).toBe(true);
  });

  it('colours a message by its provenance, and by role when it has none', () => {
    expect(messageFamily(joined)).toBe('system');
    expect(messageFamily(msg(1, { role: 'tool' }))).toBe('result');
    expect(
      messageFamily(
        msg(
          2,
          { role: 'user' },
          {
            segments: [
              {
                src: 4,
                start: 0,
                end: 5,
                kind: 'nudge',
                label: 'Force-tool nudge',
                toolName: null,
                clipped: false,
              },
            ],
          },
        ),
      ),
    ).toBe('loop');
  });
});

describe('popoverFor', () => {
  const messages = [
    msg(0, { role: 'assistant', content: '', tool_calls: [call('a', 'files.read')] }),
    msg(1, { role: 'tool', content: 'line\n'.repeat(20), tool_call_id: 'a' }),
    msg(2, { role: 'user', content: 'Call the tool now.' }, { fromRole: 'system' }),
  ];
  const d = detail(messages);
  const p = pairToolCalls(messages);

  it('says what a tool result answers, and clips the preview', () => {
    const pop = popoverFor(d, 1, p, new Set([1]))!;
    expect(pop.title).toBe('#1 tool');
    expect(pop.partner).toBe('answers #0 · files.read');
    expect(pop.status).toBe('new');
    expect(pop.preview).toHaveLength(12);
    expect(pop.more).toBe(9);
  });

  it('previews an assistant message by its calls when it has no text', () => {
    const pop = popoverFor(d, 0, p, new Set())!;
    expect(pop.partner).toBe('answered by #1');
    expect(pop.status).toBe('carried');
    expect(pop.preview.join('\n')).toContain('files.read');
  });

  it('says when the wire changed the role', () => {
    expect(popoverFor(d, 2, p, null)!.roleNote).toBe('assembled as system, sent as user');
    expect(popoverFor(d, 2, p, null)!.status).toBeNull();
  });
});

describe('resolveFocus', () => {
  const summary = (
    id: string,
    round: number,
    attempt: number,
    ts: number,
    toolNames: string[] = [],
  ) => ({ ...detail([]), id, round, attempt, ts, toolNames }) as RequestDetail;
  const calls = [
    summary('a', 0, 0, 100, ['files.read']),
    summary('b', 1, 0, 105, []),
    summary('c', 1, 1, 108, ['files.read']),
    summary('d', 2, 0, 112, []),
  ];

  it('takes an id outright', () => {
    expect(resolveFocus(calls, { requestId: 'b' })).toBe('b');
  });

  it('finds the call whose response asked for the step’s tool, the retry winning', () => {
    expect(resolveFocus(calls, { round: 1, toolName: 'files.read' })).toBe('c');
  });

  it('finds the call in the round that started closest before a wire event', () => {
    expect(resolveFocus(calls, { round: 1, ts: 106 })).toBe('b');
    expect(resolveFocus(calls, { round: 1, ts: 109 })).toBe('c');
  });

  it('falls back to the round’s last call, and to nothing for an unknown round', () => {
    expect(resolveFocus(calls, { round: 1 })).toBe('c');
    expect(resolveFocus(calls, { round: 9 })).toBeNull();
    expect(resolveFocus(calls, {})).toBeNull();
  });
});

describe('the response side', () => {
  it('finds the results that answered a response in the next request', () => {
    const response = { role: 'assistant', content: '', tool_calls: [call('a', 'x')] };
    const next = detail([
      msg(0, { role: 'user', content: 'q' }),
      msg(1, response),
      msg(2, { role: 'tool', content: 'X', tool_call_id: 'a' }),
    ]);
    expect(answersIn(response, next).map((m) => m.index)).toEqual([2]);
    expect(answersIn(response, null)).toEqual([]);
  });

  it('reports the template gap only when the provider counted', () => {
    expect(templateGap(400, 431)).toBe(31);
    expect(templateGap(400, null)).toBeNull();
  });
});
