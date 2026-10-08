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
export const DEFAULT_REACTIONS = [
  '❤️',
  '😂',
  '👍',
  '🙌',
  '👏',
  '🔥',
  '✨',
  '💯',
  '🎉',
  '😮',
  '🙏',
  '👀',
];

/**
 * Floaters kept on screen at once. A room where someone holds a reaction down (or a
 * GIF is spammed) otherwise grows the DOM by one animated node per message, each
 * living for seconds, until the pane stutters.
 */
export const MAX_FLOATERS = 30;

export interface ParsedReaction {
  emoji?: string;
  gifUrl?: string;
  /**
   * The user a reaction is aimed at. A room-wide reaction leaves this unset
   * (`target_user_id: null` on the wire); a reaction sent *at* one person carries
   * their id here, so the floater can be anchored on that person's tile — the "target"
   * visible in the room. Still delivered on the room-wide channel, so every client
   * anchors it the same way.
   */
  targetUserId?: number;
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
/**
 * Clubhouse broadcasts a GIF reaction as a bare Giphy id (the `giphy_id` field its
 * composer sends), not a full URL — so a client that only looks for a URL renders
 * nothing, which is why received GIFs went unseen. A Giphy id reconstructs to a media
 * URL on `media.giphy.com`, which is already on the allowlist. The id is validated as
 * Giphy's own alphanumeric token so nothing attacker-controlled lands in the path.
 */
const GIPHY_ID_KEYS = ['giphy_id', 'giphyId', 'gif_id', 'gifId'];
const GIPHY_ID = /^[A-Za-z0-9]{6,64}$/;

function giphyUrlFromId(obj: Record<string, unknown>, allowBareId = false): string | null {
  // A bare `id` is only a Giphy id inside a gif sub-object; on the top-level message
  // `id` is the message id, so it is read only when `allowBareId` is set.
  const keys = allowBareId ? [...GIPHY_ID_KEYS, 'id'] : GIPHY_ID_KEYS;
  for (const key of keys) {
    const v = obj[key];
    if (typeof v === 'string' && GIPHY_ID.test(v)) {
      return `https://media.giphy.com/media/${v}/giphy.gif`;
    }
  }
  return null;
}
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

/**
 * Keys that describe the *sender*, never the reaction. A real reaction event carries
 * `action_user_profile` with the sender's `photo_url` on Clubhouse's own CDN — which is
 * on the GIF allowlist, so scanning it would render someone's avatar as a "GIF".
 */
const SENDER_KEYS =
  /^(action_user_profile|user_profile|user|from_.*|.*photo.*|.*avatar.*|.*profile.*)$/i;

/** Every `[key, string]` reachable in a payload, skipping the sender's block. */
function leafStrings(value: unknown, depth = 0, out: [string, string][] = []): [string, string][] {
  const rec = asRecord(value);
  if (!rec || depth > 4) return out;
  for (const [key, v] of Object.entries(rec)) {
    if (SENDER_KEYS.test(key)) continue;
    if (typeof v === 'string') out.push([key, v]);
    else leafStrings(v, depth + 1, out);
  }
  return out;
}

/**
 * Candidate image URLs in a GIF payload, best first: the fields providers are known to
 * use, then — for a reaction event whose field names were never documented — any whole
 * string that is an https URL.
 */
export function findGifUrls(msg: Record<string, unknown>, scan = true): string[] {
  const urls: string[] = [];
  const direct = (obj: Record<string, unknown>) => {
    for (const key of URL_KEYS) {
      const v = obj[key];
      if (typeof v === 'string' && v.startsWith('http')) urls.push(v);
    }
  };

  direct(msg);
  const topId = giphyUrlFromId(msg);
  if (topId) urls.push(topId);
  for (const key of ['gif', 'gif_reaction', 'media']) {
    const nested = asRecord(msg[key]);
    if (!nested) continue;
    direct(nested);
    const nestedId = giphyUrlFromId(nested, true);
    if (nestedId) urls.push(nestedId);
    const images = asRecord(nested.images);
    if (images) {
      for (const rendition of RENDITION_KEYS) {
        const r = asRecord(images[rendition]);
        if (r && typeof r.url === 'string') urls.push(r.url);
      }
    }
  }
  if (scan) {
    for (const [, v] of leafStrings(msg)) {
      if (/^https:\/\/\S+$/.test(v)) urls.push(v);
    }
  }
  return [...new Set(urls)];
}

/** The first plausible image URL inside a GIF payload, or `null`. */
export function extractGifUrl(msg: Record<string, unknown>): string | null {
  return findGifUrls(msg, false)[0] ?? null;
}

const EMOJI = /\p{Extended_Pictographic}/u;

/**
 * Longest reaction string we will render as a floater. The room can send any reaction
 * it likes and we show it verbatim, but an unbounded string would let one message grow
 * the DOM without limit (MAX_FLOATERS caps the *count*, not each node's size). Generous
 * enough for any real reaction, including a short text one, and the floater is a React
 * text node so the content is escaped, never interpreted.
 */
export const MAX_REACTION_LEN = 200;

/**
 * The reaction payload's own content string. The content fields a reaction event is
 * known to carry — in order — then, for the undocumented key the real
 * `new_channel_reaction` uses, any string that *is* an emoji. The explicit content
 * fields are taken as-is (emoji or text) so we render whatever the room sent; the
 * fallback stays emoji-gated so a structural field (`channel`, `message_id`) is never
 * mistaken for a reaction. The sender's block is never read.
 */
function findEmoji(msg: Record<string, unknown>): string | null {
  for (const key of ['emoji', 'reaction', 'message']) {
    const v = msg[key];
    if (typeof v === 'string' && v.trim() && v.trim().length <= MAX_REACTION_LEN) {
      return v.trim();
    }
  }
  for (const [, v] of leafStrings(msg)) {
    const t = v.trim();
    if (t.length > 0 && t.length <= MAX_REACTION_LEN && EMOJI.test(t)) return t;
  }
  return null;
}

/**
 * What a room message is, if it is a reaction.
 *
 * Seen on the wire: `{action: 'new_channel_reaction', action_user_profile, channel,
 * message_id, message: '', …}`. A GIF is read from a URL anywhere in the payload (never
 * the sender's block) and must be on the allowlist; one that is not returns `null`, not
 * an empty floater, so nothing renders for it. A message with no action only counts if
 * it carries the old explicit `emoji` / `reaction` field — scanning bare messages would
 * turn a chat line containing a link or an emoji into a reaction.
 */
export function parseRoomReaction(msg: Record<string, unknown>): ParsedReaction | null {
  const action = typeof msg.action === 'string' ? msg.action : '';
  const gifish = /gif/i.test(action) || 'gif' in msg || 'gif_url' in msg || 'gifUrl' in msg;
  const reactionish = action === 'react' || /reaction/i.test(action);

  // The person a reaction is aimed at. `target_user_id` is null for a room-wide one.
  const rawTarget = msg.target_user_id;
  const targetUserId =
    typeof rawTarget === 'number' && Number.isFinite(rawTarget) ? rawTarget : undefined;
  const withTarget = (r: ParsedReaction): ParsedReaction =>
    targetUserId != null ? { ...r, targetUserId } : r;

  if (gifish || reactionish) {
    const gif = findGifUrls(msg).find(isAllowedGifUrl);
    if (gif) return withTarget({ gifUrl: gif });
    if (gifish) return null;
  }

  if (reactionish) {
    const emoji = findEmoji(msg);
    return emoji ? withTarget({ emoji }) : null;
  }
  if (!action) {
    const emoji = msg.emoji ?? msg.reaction;
    if (typeof emoji === 'string' && emoji.trim() && emoji.trim().length <= MAX_REACTION_LEN) {
      return { emoji: emoji.trim() };
    }
  }
  return null;
}
