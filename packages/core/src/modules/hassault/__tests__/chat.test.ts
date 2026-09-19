import { describe, expect, it } from 'vitest';

import { MAX_BYTES, MAX_GRAPHEMES, clampChat, graphemeCount } from '../chat-text';
import { MatchSession, parseChatLine } from '../session';

const FAMILY = '\u{1F468}‍\u{1F469}‍\u{1F467}‍\u{1F466}';
const FLAG = '\u{1F1FA}\u{1F1F8}';
const THUMB = '\u{1F44D}\u{1F3FD}';

describe('chat text', () => {
  it('counts what a person sees, not UTF-16 units', () => {
    expect(FAMILY.length).toBe(11);
    expect(graphemeCount(FAMILY)).toBe(1);
    expect(graphemeCount(`${FLAG}${THUMB}é`)).toBe(3);
    expect(graphemeCount('日本語')).toBe(3);
  });

  it('leaves text that fits exactly as typed', () => {
    for (const t of [FAMILY, FLAG, 'مرحبا', 'gg 🎉', 'नमस्ते']) expect(clampChat(t)).toBe(t);
  });

  it('never cuts inside an emoji', () => {
    const out = clampChat(FAMILY.repeat(MAX_GRAPHEMES));
    // 25 UTF-8 bytes each, so the byte ceiling decides, and each one is whole.
    expect(out).toBe(FAMILY.repeat(Math.floor(MAX_BYTES / 25)));
  });

  it('caps at the grapheme limit', () => {
    expect(graphemeCount(clampChat('é'.repeat(500)))).toBe(MAX_GRAPHEMES);
  });
});

describe('chat lines', () => {
  it('reads a server line', () => {
    const line = parseChatLine({
      id: 'abc',
      ts: 1,
      senderId: 'p1',
      senderName: 'rob',
      team: 1,
      isTeam: true,
      text: `hi ${FAMILY}`,
    });
    expect(line).toMatchObject({ id: 'abc', team: 1, isTeam: true, text: `hi ${FAMILY}` });
  });

  it('ignores an empty line', () => {
    expect(parseChatLine({ text: '   ' })).toBeNull();
  });

  it('shows a line delivered twice once', () => {
    const session = new MatchSession();
    session.state.status = 'joined';
    const deliver = (session as unknown as { receive: (m: unknown) => void }).receive.bind(session);
    const frame = {
      event: 'chat',
      data: { id: 'same', ts: 1, senderId: 'p', senderName: 'x', team: 0, text: 'yo' },
    };
    deliver(frame);
    deliver(frame);
    expect(session.state.chat.map((l) => l.id)).toEqual(['same']);
  });
});
