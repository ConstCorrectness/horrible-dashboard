import { describe, expect, it } from 'vitest';

import { extractGifUrl, isAllowedGifUrl, parseRoomReaction } from '../reactions';

describe('which GIF hosts may render', () => {
  it('allows the providers and Clubhouse itself', () => {
    expect(isAllowedGifUrl('https://media.giphy.com/media/abc/giphy.gif')).toBe(true);
    expect(isAllowedGifUrl('https://media.tenor.com/x/y.gif')).toBe(true);
    expect(isAllowedGifUrl('https://clubhouse-prod.s3.amazonaws.com/a.gif')).toBe(true);
  });

  it('refuses a stranger’s host, because the sender picks the URL', () => {
    expect(isAllowedGifUrl('https://attacker.example/track.gif')).toBe(false);
    // The suffix match must be on a label boundary, or this passes.
    expect(isAllowedGifUrl('https://evilgiphy.com/a.gif')).toBe(false);
    expect(isAllowedGifUrl('https://giphy.com.attacker.example/a.gif')).toBe(false);
  });

  it('refuses anything that is not https', () => {
    expect(isAllowedGifUrl('http://media.giphy.com/a.gif')).toBe(false);
    expect(isAllowedGifUrl('data:image/gif;base64,R0lGODlh')).toBe(false);
    expect(isAllowedGifUrl('javascript:alert(1)')).toBe(false);
    expect(isAllowedGifUrl('not a url')).toBe(false);
  });
});

describe('finding the URL in a GIF payload', () => {
  it('reads a flat field', () => {
    expect(extractGifUrl({ gif_url: 'https://media.giphy.com/a.gif' })).toBe(
      'https://media.giphy.com/a.gif',
    );
  });

  it('reads a nested provider payload, preferring a small rendition', () => {
    expect(
      extractGifUrl({
        gif: {
          images: {
            original: { url: 'https://media.giphy.com/big.gif' },
            fixed_height_small: { url: 'https://media.giphy.com/small.gif' },
          },
        },
      }),
    ).toBe('https://media.giphy.com/small.gif');
  });

  it('returns null rather than guessing', () => {
    expect(extractGifUrl({ gif: { id: 'abc' } })).toBeNull();
    expect(extractGifUrl({ text: 'hi' })).toBeNull();
  });
});

describe('reading a reaction off a room message', () => {
  it('reads an emoji reaction', () => {
    expect(parseRoomReaction({ action: 'react', emoji: '🔥' })).toEqual({ emoji: '🔥' });
    expect(parseRoomReaction({ action: 'emoji_reaction', emoji: '👏' })).toEqual({ emoji: '👏' });
    expect(parseRoomReaction({ reaction: '❤️' })).toEqual({ emoji: '❤️' });
  });

  it('reads a GIF reaction from an allowed host', () => {
    expect(
      parseRoomReaction({ action: 'gif_reaction', gif_url: 'https://media.giphy.com/a.gif' }),
    ).toEqual({ gifUrl: 'https://media.giphy.com/a.gif' });
  });

  it('drops a GIF from a host that is not allowed, instead of showing nothing-in-a-box', () => {
    expect(
      parseRoomReaction({ action: 'gif_reaction', gif_url: 'https://attacker.example/t.gif' }),
    ).toBeNull();
  });

  it('does not mistake other room events or chat for a reaction', () => {
    expect(parseRoomReaction({ action: 'join_channel', user_id: 1 })).toBeNull();
    expect(parseRoomReaction({ action: 'chat_message', text: 'hello 🔥' })).toBeNull();
    expect(parseRoomReaction({ text: 'hello' })).toBeNull();
  });

  it('renders a reaction verbatim — text or emoji — but caps its length', () => {
    // "Allow any reactions": a reaction event's content is shown as-is (the floater is
    // an escaped React text node), so a short text reaction renders instead of dropping.
    expect(parseRoomReaction({ action: 'react', emoji: 'gg' })).toEqual({ emoji: 'gg' });
    // Past the cap it is still dropped, so one message cannot grow the DOM without bound.
    expect(parseRoomReaction({ action: 'react', emoji: 'x'.repeat(201) })).toBeNull();
  });
});

describe('the real new_channel_reaction event', () => {
  // The shape seen on the wire (2026-10-04): the sender lives under
  // `action_user_profile`, and `message` is empty.
  const base = {
    action: 'new_channel_reaction',
    action_user_profile: {
      user_id: 774824534,
      name: 'Someone',
      photo_url: 'https://clubhouseprod.s3.amazonaws.com/avatars/someone.jpg',
    },
    channel: 'M4GkVV5y',
    message_id: '38f9befd-12a2-4c5b-b028-216fdc9870de',
    message: '',
  };

  it('finds the emoji under a key nobody documented', () => {
    expect(parseRoomReaction({ ...base, reaction_emoji: '🔥' })).toEqual({ emoji: '🔥' });
    expect(parseRoomReaction({ ...base, emoji_reaction: { value: '👏' } })).toEqual({
      emoji: '👏',
    });
  });

  it('does not read the sender’s avatar as a GIF — it is on the allowed CDN', () => {
    expect(parseRoomReaction({ ...base, reaction_emoji: '❤️' })).toEqual({ emoji: '❤️' });
    expect(parseRoomReaction(base)).toBeNull();
  });

  it('reads a GIF carried under the same action, from any field', () => {
    expect(
      parseRoomReaction({
        ...base,
        content: { media: 'https://media.giphy.com/media/x/giphy.gif' },
      }),
    ).toEqual({ gifUrl: 'https://media.giphy.com/media/x/giphy.gif' });
  });

  it('does not turn a chat line into a reaction', () => {
    // No action: only the old explicit emoji/reaction fields count.
    expect(parseRoomReaction({ text: 'https://media.giphy.com/x.gif' })).toBeNull();
    expect(parseRoomReaction({ message: 'nice 🔥 one' })).toBeNull();
    expect(parseRoomReaction({ action: 'chat_message', text: '🔥' })).toBeNull();
  });

  // Documents the mechanism behind the "white text sprayed over a room" screenshot:
  // Clubhouse's server forwards the `emoji` field unverified and its client draws any
  // string as a glyph, so a crafted reaction event renders arbitrary text. This is a
  // pure, in-process check against OUR parser — it sends nothing and touches no live
  // room — proving what the official app does and why the scary "[DEMO] Leaked …" text
  // appears. It is not a payload sender; the backend validator refuses to emit this.
  it('shows how a crafted reaction event renders as text (no network)', () => {
    const fakeLeak = '[DEMO] Leaked J.DOE IBAN:XX00 CVV:*** SSN:XXX';
    // The text can arrive in either content field the event is known to carry.
    expect(parseRoomReaction({ ...base, emoji: fakeLeak })).toEqual({ emoji: fakeLeak });
    expect(parseRoomReaction({ ...base, message: fakeLeak })).toEqual({ emoji: fakeLeak });
    // A payload past the render cap is dropped, so it can never grow the DOM unbounded.
    expect(parseRoomReaction({ ...base, emoji: 'X'.repeat(500) })).toBeNull();
  });
});
