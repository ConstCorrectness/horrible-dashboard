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

  it('refuses an emoji field that is really text', () => {
    expect(parseRoomReaction({ action: 'react', emoji: 'x'.repeat(200) })).toBeNull();
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
});
