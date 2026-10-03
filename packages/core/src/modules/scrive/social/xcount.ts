/**
 * X's character count, as the composer shows it while you type. Mirrors
 * `x_length` / `x_thread` / `x_cost` in backend/modules/scrive/social.py, which is the
 * one that decides at approval; the tests pin both to the same cases.
 *
 * twitter-text's weighting: code points in a few ranges (Latin, Greek, Cyrillic,
 * Hebrew, Arabic… and general punctuation) weigh 1, everything else (CJK, emoji) 2;
 * every URL counts 23 however long it is; joiners and skin-tone modifiers ride on the
 * emoji before them. The limit is 280.
 */
import type { XPayload, XPostDraft } from '../api';

export const X_LIMIT = 280;
export const X_URL_LENGTH = 23;
/** Pay-per-use prices (Feb 2026); the backend's estimate uses the same. */
export const X_PRICE_POST = 0.015;
export const X_PRICE_URL_POST = 0.2;
export const POST_URL = '{{post.url}}';

const URL = /https?:\/\/[^\s<>"']+|\{\{\s*post\.url\s*\}\}/gi;
const LIGHT: [number, number][] = [
  [0x0000, 0x10ff],
  [0x2000, 0x200d],
  [0x2010, 0x201f],
  [0x2032, 0x2037],
];
/** Joiners, variation selectors and skin-tone modifiers: they ride on the emoji before them. */
function isRider(code: number): boolean {
  return (
    code === 0x200d || code === 0xfe0e || code === 0xfe0f || (code >= 0x1f3fb && code <= 0x1f3ff)
  );
}

function weight(ch: string): number {
  const code = ch.codePointAt(0) ?? 0;
  return LIGHT.some(([lo, hi]) => code >= lo && code <= hi) ? 100 : 200;
}

function plainWeight(text: string): number {
  let total = 0;
  let previousHeavy = false;
  for (const ch of text) {
    if (previousHeavy && isRider(ch.codePointAt(0) ?? 0)) continue;
    const w = weight(ch);
    total += w;
    previousHeavy = w === 200;
  }
  return total;
}

/** Weighted length as X counts it. */
export function xLength(text: string): number {
  const normal = text.normalize('NFC');
  let total = 0;
  let last = 0;
  for (const match of normal.matchAll(URL)) {
    total += plainWeight(normal.slice(last, match.index)) + X_URL_LENGTH * 100;
    last = (match.index ?? 0) + match[0].length;
  }
  total += plainWeight(normal.slice(last));
  return Math.ceil(total / 100);
}

/** The posts as they will be sent, with the link placed. */
export function xThread(payload: XPayload, linkInReplyDefault = true): XPostDraft[] {
  const posts = payload.posts.filter((p) => p.text.trim() || p.media.length).map((p) => ({ ...p }));
  const link = payload.link.trim();
  const inReply = payload.link_in_reply ?? linkInReplyDefault;
  if (link) {
    if (inReply || !posts.length) posts.push({ text: link, media: [] });
    else posts[0] = { ...posts[0], text: `${posts[0].text.trimEnd()}\n\n${link}` };
  }
  return posts;
}

/** `{usd, posts, withUrl}` — what X would charge for the thread. */
export function xCost(
  payload: XPayload,
  linkInReplyDefault = true,
): { usd: number; posts: number; withUrl: number } {
  const posts = xThread(payload, linkInReplyDefault);
  const withUrl = posts.filter((p) => new RegExp(URL.source, 'i').test(p.text)).length;
  const usd = withUrl * X_PRICE_URL_POST + (posts.length - withUrl) * X_PRICE_POST;
  return { usd: Math.round(usd * 1000) / 1000, posts: posts.length, withUrl };
}
