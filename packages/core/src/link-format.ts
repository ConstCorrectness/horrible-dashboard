/**
 * How a shareable link describes itself: time left, and the message a person
 * pastes to somebody else.
 *
 * Pure and in its own file for the `relay-status.ts` reason — the components that
 * use it own clipboards and timers and cannot be imported in a unit test, and
 * the wording is precisely the part worth pinning.
 */

/** Below this, the expiry chip turns into a warning. */
export const EXPIRY_WARN_S = 10 * 60;

/** Whole seconds until `expiresAt` (epoch seconds), or null when it never expires. */
export function secondsLeft(expiresAt: number, nowMs: number = Date.now()): number | null {
  if (!expiresAt) return null;
  return Math.floor(expiresAt - nowMs / 1000);
}

/**
 * "3h 41m", "12m", "under a minute", "expired" — or null for a link with no expiry.
 *
 * Minutes, never seconds: this is read at a glance, and a number that changes
 * every second is a number that invites watching instead of acting.
 */
export function formatRemaining(expiresAt: number, nowMs: number = Date.now()): string | null {
  const left = secondsLeft(expiresAt, nowMs);
  if (left === null) return null;
  if (left <= 0) return 'expired';
  const hours = Math.floor(left / 3600);
  const minutes = Math.floor((left % 3600) / 60);
  if (hours > 0) return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  if (minutes > 0) return `${minutes}m`;
  return 'under a minute';
}

/** The URL as a person reads it: no scheme, which carries no information here. */
export function displayUrl(url: string): string {
  return url.replace(/^https?:\/\//, '');
}

export interface InviteOptions {
  url: string;
  /** What the link is for, e.g. "Watch my screen". */
  lead?: string;
  expiresAt?: number;
  /** Whether the link needs a passphrase. The passphrase itself is never included. */
  passphrase?: boolean;
  nowMs?: number;
}

/**
 * The message to paste into a chat.
 *
 * It says a passphrase is needed and **never** contains it: the invite is the
 * thing that gets forwarded, screenshotted and pasted into the wrong channel,
 * and a passphrase that travels with the link protects nothing.
 */
export function inviteText({
  url,
  lead = 'Join me',
  expiresAt = 0,
  passphrase = false,
  nowMs = Date.now(),
}: InviteOptions): string {
  const lines = [`${lead}: ${url}`];
  const remaining = formatRemaining(expiresAt, nowMs);
  if (remaining && remaining !== 'expired') lines.push(`The link expires in ${remaining}.`);
  if (passphrase) lines.push('It needs a passphrase — ask me for it.');
  return lines.join('\n');
}
