/**
 * Profiles, artwork and comment walls — the part of a player other people can see.
 *
 * Everything here is proxied through the node (`/api/games/profile/…`) so the
 * browser talks to one origin and never handles the game-server bearer token, the
 * same custody rule sign-in follows.
 *
 * **Your own** profile still arrives over the `games` `/ws` channel as live state
 * (`profileGet`/`profileSet` in game-ws.ts). These are for everyone else's, plus
 * the writes that carry a file. That split is deliberate: a profile readable only
 * over a game socket is a profile readable only while playing, which is how the
 * Plaza ended up rendering the same invented bio for every player.
 */
import { apiDelete, apiGet, apiPost } from '../../api';

/** One pinned showcase on a profile. Open-ended by design — a showcase is a
 * `{kind, ...}` the profile page knows how to render, and adding a kind should not
 * mean a schema migration. */
export interface Showcase {
  kind: string;
  [key: string]: unknown;
}

export interface PlayerProfile {
  account_id: string;
  handle: string | null;
  display_name: string;
  /** The inline fallback: an emoji, capped at 8 characters server-side. */
  avatar: string;
  /** An uploaded image, when there is one. Prefer this over `avatar` when set. */
  avatar_url: string | null;
  background_url: string | null;
  /** A named preset background, when the player picked one instead of uploading. */
  background_id: string | null;
  status_text: string;
  showcase: Showcase[];
  bio: string;
  xp: number;
  level: number;
  level_floor: number;
  next_level_xp: number | null;
}

export interface ProfileComment {
  id: string;
  account_id: string;
  author_id: string;
  author_name: string | null;
  author_handle: string | null;
  author_avatar: string;
  author_avatar_url: string | null;
  body: string;
  created_at: number;
}

/** Patch shape — every field optional, absent means "leave it alone". Clearing
 * artwork is an explicit `''`, never `null`. */
export interface ProfilePatch {
  avatar?: string;
  bio?: string;
  avatar_url?: string;
  background_url?: string;
  background_id?: string;
  status_text?: string;
  showcase?: Showcase[];
}

/**
 * Turn a stored media reference into something an `<img>` can load.
 *
 * The server hands back `/media/<sha>`; the node re-serves it at
 * `/api/games/profile/media/<sha>`. Anything already absolute (a preset shipped
 * with the app, or a full URL) is passed through untouched.
 */
export function mediaUrl(ref: string | null | undefined): string | null {
  if (!ref) return null;
  if (ref.startsWith('http://') || ref.startsWith('https://') || ref.startsWith('data:')) {
    return ref;
  }
  const sha = ref.replace(/^\/media\//, '');
  return `/api/games/profile/media/${encodeURIComponent(sha)}`;
}

export async function fetchProfile(handle: string): Promise<PlayerProfile | null> {
  const res = await apiGet<{ profile?: PlayerProfile; error?: string }>(
    `/games/profile/${encodeURIComponent(handle)}`,
  );
  return res.profile ?? null;
}

/** The slice of a profile a *list row* needs. Deliberately not a `PlayerProfile`:
 * a friends list wants a face and a level, and fetching whole profiles to render
 * one line each is what makes a roster slow. */
export interface ProfileCard {
  handle: string;
  display_name: string;
  avatar: string;
  avatar_url: string | null;
  status_text: string;
  level: number;
  xp: number;
}

/**
 * Avatar/level/status for many callsigns in one request, keyed by handle.
 *
 * Unknown handles are simply absent — a friend who has never signed in to the
 * game server is normal, not an error, and the roster still renders them from
 * local data.
 */
export async function fetchProfileCards(handles: string[]): Promise<Record<string, ProfileCard>> {
  const wanted = handles.filter(Boolean);
  if (wanted.length === 0) return {};
  const res = await apiPost<{ cards?: Record<string, ProfileCard>; error?: string }>(
    '/games/profiles/cards',
    { handles: wanted },
  );
  return res.cards ?? {};
}

export async function patchProfile(patch: ProfilePatch): Promise<PlayerProfile | null> {
  const res = await apiPost<{ profile?: PlayerProfile; error?: string }>('/games/profile', patch);
  if (res.error) throw new Error(res.error);
  return res.profile ?? null;
}

export interface UploadResult {
  sha256: string;
  /** The server-relative `/media/<sha>` reference to store on the profile. */
  url: string;
  mime: string;
  bytes: number;
}

/**
 * Upload a profile image.
 *
 * Sends the **file itself**, with its real MIME as the Content-Type. It used to
 * send a base64 data URL into the `avatar` column, which the server truncates to 8
 * characters — so every upload silently became eight bytes of base64 and the
 * picture never appeared. The server now checks the declared type against the
 * bytes' magic number, so a data URL would be rejected outright rather than stored
 * wrong.
 */
export async function uploadProfileImage(
  file: File,
  kind: 'avatar' | 'background',
): Promise<UploadResult> {
  const res = await fetch(`/api/games/profile/media?kind=${kind}`, {
    method: 'POST',
    headers: { 'Content-Type': file.type },
    body: file,
  });
  const data = (await res.json()) as Partial<UploadResult> & { error?: string };
  if (data.error) throw new Error(data.error);
  if (!data.sha256 || !data.url) throw new Error('upload failed');
  return data as UploadResult;
}

export async function fetchComments(handle: string, before?: number): Promise<ProfileComment[]> {
  const query = before === undefined ? '' : `?before=${before}`;
  const res = await apiGet<{ comments?: ProfileComment[]; error?: string }>(
    `/games/profile/${encodeURIComponent(handle)}/comments${query}`,
  );
  return res.comments ?? [];
}

export async function addComment(handle: string, body: string): Promise<ProfileComment> {
  const res = await apiPost<{ comment?: ProfileComment; error?: string }>(
    `/games/profile/${encodeURIComponent(handle)}/comments`,
    { body },
  );
  if (res.error) throw new Error(res.error);
  if (!res.comment) throw new Error('could not post that comment');
  return res.comment;
}

export async function hideComment(commentId: string): Promise<void> {
  const res = await apiDelete<{ ok?: boolean; error?: string }>(
    `/games/profile/comments/${encodeURIComponent(commentId)}`,
  );
  if (res.error) throw new Error(res.error);
}

/**
 * The preset backgrounds, for players who don't want to upload one.
 *
 * CSS gradients rather than image files, deliberately: they cost no bytes, no
 * volume space and no moderation, they scale to any pane width, and they can't be
 * someone else's copyright — the same rule the maps and audio in HorribleAssault
 * follow. An uploaded image overrides whichever of these is picked.
 */
export const BACKGROUND_PRESETS: { id: string; label: string; css: string }[] = [
  {
    id: 'ember',
    label: 'Ember',
    css: 'linear-gradient(135deg, #2b1408 0%, #7a2f0e 55%, #c2560f 100%)',
  },
  {
    id: 'cyan',
    label: 'Cyan',
    css: 'linear-gradient(135deg, #04212b 0%, #0b5468 55%, #12a3c2 100%)',
  },
  {
    id: 'violet',
    label: 'Violet',
    css: 'linear-gradient(135deg, #1a0b2e 0%, #4a1d76 55%, #7b3fe4 100%)',
  },
  {
    id: 'moss',
    label: 'Moss',
    css: 'linear-gradient(135deg, #0d1f14 0%, #1f4a2c 55%, #3f9c5c 100%)',
  },
  {
    id: 'slate',
    label: 'Slate',
    css: 'linear-gradient(135deg, #14171c 0%, #2b323d 55%, #4a5568 100%)',
  },
  {
    id: 'rose',
    label: 'Rose',
    css: 'linear-gradient(135deg, #2b0a16 0%, #7a1533 55%, #d13a63 100%)',
  },
];

export function backgroundCss(id: string | null | undefined): string | null {
  return BACKGROUND_PRESETS.find((p) => p.id === id)?.css ?? null;
}
