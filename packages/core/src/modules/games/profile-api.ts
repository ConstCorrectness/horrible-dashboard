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
import { apiUrl } from '../../origin';
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

export interface ProfileCard {
  account_id: string;
  handle: string;
  display_name: string;
  avatar: string;
  avatar_url: string | null;
  status_text: string;
  level: number;
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
  avatar_url?: string;
  display_name?: string;
  status_text?: string;
  bio?: string;
  background_id?: string;
  background_url?: string;
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

export interface BackgroundPreset {
  id: string;
  label: string;
  css: string;
}

/** Shipped backgrounds: purely CSS, no asset downloads, available offline. */
export const BACKGROUND_PRESETS: BackgroundPreset[] = [
  { id: 'slate', label: 'Slate', css: 'linear-gradient(135deg, #1e293b 0%, #0f172a 100%)' },
  { id: 'ember', label: 'Ember', css: 'linear-gradient(135deg, #451a03 0%, #7c2d12 50%, #1c1917 100%)' },
  { id: 'aurora', label: 'Aurora', css: 'linear-gradient(135deg, #064e3b 0%, #0c4a6e 50%, #0f172a 100%)' },
  { id: 'neon', label: 'Neon', css: 'linear-gradient(135deg, #581c87 0%, #831843 50%, #0f172a 100%)' },
  { id: 'terminal', label: 'Terminal', css: 'linear-gradient(135deg, #022c22 0%, #052e16 50%, #000000 100%)' },
  { id: 'gold', label: 'Gold', css: 'linear-gradient(135deg, #713f12 0%, #854d0e 50%, #1c1917 100%)' },
];

export function backgroundCss(presetId: string | null | undefined): string | null {
  if (!presetId) return null;
  const p = BACKGROUND_PRESETS.find((x) => x.id === presetId);
  return p ? p.css : null;
}

export async function fetchProfile(handle: string): Promise<PlayerProfile | null> {
  const norm = handle.replace(/^@/, '');
  try {
    return await apiGet<PlayerProfile>(`/games/profile/${encodeURIComponent(norm)}`);
  } catch {
    return null;
  }
}

export async function fetchProfileCards(
  handles: string[],
): Promise<Record<string, ProfileCard>> {
  if (handles.length === 0) return {};
  try {
    const raw = await apiPost<{ cards: Record<string, ProfileCard> }>(
      '/games/profile/cards',
      { handles },
    );
    return raw.cards ?? {};
  } catch {
    return {};
  }
}

export async function patchProfile(patch: ProfilePatch): Promise<PlayerProfile> {
  return apiPost<PlayerProfile>('/games/profile/me', patch);
}

export async function uploadProfileImage(
  file: File,
  kind: 'avatar' | 'background',
): Promise<{ url: string }> {
  const form = new FormData();
  form.append('file', file);
  form.append('kind', kind);

  const res = await fetch(apiUrl('/games/profile/upload'), {
    method: 'POST',
    body: form,
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string; error?: string };
    throw new Error(body.detail || body.error || `Upload failed: ${res.statusText}`);
  }
  return res.json() as Promise<{ url: string }>;
}

export async function fetchComments(handle: string): Promise<ProfileComment[]> {
  const norm = handle.replace(/^@/, '');
  const r = await apiGet<{ comments: ProfileComment[] }>(
    `/games/profile/${encodeURIComponent(norm)}/comments`,
  );
  return r.comments ?? [];
}

export async function addComment(
  handle: string,
  body: string,
): Promise<ProfileComment> {
  const norm = handle.replace(/^@/, '');
  return apiPost<ProfileComment>(`/games/profile/${encodeURIComponent(norm)}/comments`, {
    body,
  });
}

export async function hideComment(
  commentId: string,
): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(
    `/games/profile/comments/${encodeURIComponent(commentId)}`,
  );
}
