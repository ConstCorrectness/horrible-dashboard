/**
 * One person's face, at any size.
 *
 * Three fallbacks, in order: an uploaded image, the emoji they chose, and their
 * initial. The last one matters more than it looks — a friend who has never
 * signed in to the game server has no profile at all, and a roster full of empty
 * squares reads as broken rather than as "they haven't picked a picture".
 */
import { mediaUrl } from '../games/profile-api';

export interface AvatarProps {
  name: string;
  /** Emoji fallback (the `avatar` column), when there is one. */
  emoji?: string | null;
  /** An uploaded image reference (`/media/<sha>`), when there is one. */
  imageRef?: string | null;
  size?: number;
  online?: boolean;
  /** Show the presence dot. Off in places where presence is stated elsewhere. */
  showPresence?: boolean;
}

export function Avatar({
  name,
  emoji,
  imageRef,
  size = 32,
  online = false,
  showPresence = false,
}: AvatarProps) {
  const src = mediaUrl(imageRef);
  return (
    <span
      className="people-avatar"
      style={{ width: size, height: size, fontSize: Math.round(size * 0.5) }}
      data-online={online ? 'true' : 'false'}
    >
      {src ? (
        <img src={src} alt="" width={size} height={size} />
      ) : (
        <span aria-hidden="true">{emoji || name.slice(0, 1).toUpperCase() || '?'}</span>
      )}
      {showPresence && <span className="people-avatar-dot" />}
    </span>
  );
}
