/**
 * Reading reactions off the room's PubNub feed.
 *
 * Pure, because the shape of a Clubhouse reaction event is the part nobody has
 * documented: an emoji arrives as `react` with an `emoji`, a GIF as something with a
 * URL inside it, and neither is guaranteed to keep its field names. So this accepts
 * the shapes seen and the obvious variants, and returns `null` — never a guess — for
 * anything else. The hook logs every room message, which is how a new shape is found.
 */

/** Shown until the room's own `emoji_reaction_options` arrive on join. */
export const DEFAULT_REACTIONS = ['❤️', '😂', '👍', '🙌', '👏', '🔥'];

/**
 * Floaters kept on screen at once. A room where someone holds a reaction down (or a
 * GIF is spammed) otherwise grows the DOM by one animated node per message, each
 * living for seconds, until the pane stutters.
 */
export const MAX_FLOATERS = 30;

export interface ParsedReaction {
  emoji?: string;
  gifUrl?: string;
}

/**
 * Hosts a GIF may load from. A reaction's URL is chosen by the sender, so rendering
 * any `https:` address would hand a stranger every viewer's IP address and a way to
 * swap the picture later. An allowlist of the GIF providers and Clubhouse's own CDN
 * keeps the feature and drops the tracker; a host that is missing shows up in the
 * console (see `useClubhouseVoice`) rather than silently never rendering.
 */
const GIF_HOST_SUFFIXES = [
  'giphy.com',
  'tenor.com',
  'tenor.co',
  'clubhouse.com',
  'clubhouseapi.com',
  'clubhouse-prod.s3.amazonaws.com',
];

export function isAllowedGifUrl(raw: string): boolean {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.protocol !== 'https:') return false;
  const host = url.hostname.toLowerCase();
  return GIF_HOST_SUFFIXES.some((suffix) => host === suffix || host.endsWith(`.${suffix}`));
}

const URL_KEYS = ['gif_url', 'gifUrl', 'url', 'media_url', 'image_url', 'original_url'];
/** Providers nest the renditions; take the smallest that animates. */
const RENDITION_KEYS = [
  'fixed_height_small',
  'fixed_width_small',
  'fixed_height',
  'downsized',
  'original',
];

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** The first plausible image URL inside a GIF payload, or `null`. */
export function extractGifUrl(msg: Record<string, unknown>): string | null {
  const direct = (obj: Record<string, unknown>): string | null => {
    for (const key of URL_KEYS) {
      const v = obj[key];
      if (typeof v === 'string' && v.startsWith('http')) return v;
    }
    return null;
  };

  const top = direct(msg);
  if (top) return top;

  for (const key of ['gif', 'gif_reaction', 'media']) {
    const nested = asRecord(msg[key]);
    if (!nested) continue;
    const inNested = direct(nested);
    if (inNested) return inNested;
    const images = asRecord(nested.images);
    if (images) {
      for (const rendition of RENDITION_KEYS) {
        const r = asRecord(images[rendition]);
        if (r && typeof r.url === 'string') return r.url;
      }
    }
  }
  return null;
}

/**
 * What a room message is, if it is a reaction.
 *
 * `emoji` and `reaction` carry an emoji; anything whose action names a GIF carries a
 * URL. A GIF whose URL is not on the allowlist is returned as `null`, not as an
 * emoji-less floater, so nothing renders for it.
 */
export function parseRoomReaction(msg: Record<string, unknown>): ParsedReaction | null {
  const action = typeof msg.action === 'string' ? msg.action : '';

  if (/gif/i.test(action) || 'gif' in msg || 'gif_url' in msg || 'gifUrl' in msg) {
    const url = extractGifUrl(msg);
    return url && isAllowedGifUrl(url) ? { gifUrl: url } : null;
  }

  if (action === 'react' || /reaction/i.test(action) || !action) {
    const emoji = msg.emoji ?? msg.reaction;
    if (typeof emoji === 'string' && emoji.trim() && emoji.length <= 16) {
      return { emoji: emoji.trim() };
    }
  }
  return null;
}
