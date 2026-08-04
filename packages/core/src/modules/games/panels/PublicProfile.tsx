/**
 * Somebody else's profile — banner, face, status, bio, level, showcases, wall.
 *
 * This is the read that had no endpoint. The Plaza's player card rendered the same
 * invented paragraph for every player because there was no way to fetch anyone
 * else's bio; profiles were live state on a game socket, which meant they were
 * only readable while playing.
 *
 * It is HTTP and unauthenticated on purpose: a profile is public the same way the
 * ladder that shows someone's rating is, and it must be readable when every one of
 * that person's machines is off. Whether you are *friends* has nothing to do with
 * it — friendship governs the fabric, not the game server's public pages.
 */
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';

import { Avatar } from '../../people/Avatar';
import { backgroundCss, fetchProfile, mediaUrl, type PlayerProfile } from '../profile-api';
import { ProfileComments } from './ProfileComments';

export interface PublicProfileProps {
  handle: string;
  /** The signed-in account, so the wall knows whether it can be written on. */
  viewerAccountId?: string | null;
  onBack?: () => void;
  /** Extra actions the host pane wants on the header (Message, Invite, …). */
  actions?: ReactNode;
}

/** The banner: an uploaded image if there is one, else the chosen preset, else a
 * neutral wash. Never nothing — an empty banner reads as a failed load. */
function bannerStyle(profile: PlayerProfile): CSSProperties {
  const uploaded = mediaUrl(profile.background_url);
  if (uploaded) {
    return { backgroundImage: `url(${uploaded})`, backgroundSize: 'cover' };
  }
  return {
    background:
      backgroundCss(profile.background_id) ??
      'linear-gradient(135deg, #14171c 0%, #2b323d 55%, #4a5568 100%)',
  };
}

export function PublicProfile({ handle, viewerAccountId, onBack, actions }: PublicProfileProps) {
  const [profile, setProfile] = useState<PlayerProfile | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'missing'>('loading');

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    fetchProfile(handle)
      .then((p) => {
        if (cancelled) return;
        setProfile(p);
        setState(p ? 'ready' : 'missing');
      })
      .catch(() => {
        if (!cancelled) setState('missing');
      });
    return () => {
      cancelled = true;
    };
  }, [handle]);

  if (state === 'loading') {
    return <div className="profile-page profile-page-empty">Loading @{handle}…</div>;
  }
  if (state === 'missing' || !profile) {
    return (
      <div className="profile-page profile-page-empty">
        {onBack && (
          <button type="button" className="profile-back" onClick={onBack}>
            ← Back
          </button>
        )}
        <p>
          No profile for @{handle}. They may not have signed in to the game server — or the server
          is unreachable from here.
        </p>
      </div>
    );
  }

  const pct =
    profile.next_level_xp !== null
      ? Math.min(
          100,
          Math.round(
            ((profile.xp - profile.level_floor) / (profile.next_level_xp - profile.level_floor)) *
              100,
          ),
        )
      : 100;

  return (
    <div className="profile-page">
      <div className="profile-banner" style={bannerStyle(profile)}>
        {onBack && (
          <button type="button" className="profile-back" onClick={onBack}>
            ← Back
          </button>
        )}
      </div>

      <header className="profile-head">
        <Avatar
          name={profile.display_name}
          emoji={profile.avatar}
          imageRef={profile.avatar_url}
          size={72}
        />
        <div className="profile-head-text">
          <h3>{profile.display_name}</h3>
          <span className="profile-handle">@{profile.handle}</span>
          {profile.status_text && <p className="profile-status">{profile.status_text}</p>}
          <div className="profile-level">
            <span>Level {profile.level}</span>
            <span className="profile-xp">{profile.xp} XP</span>
          </div>
          <div className="profile-xpbar">
            <span style={{ width: `${Math.max(4, pct)}%` }} />
          </div>
        </div>
        {actions && <div className="profile-head-actions">{actions}</div>}
      </header>

      {profile.bio && <p className="profile-bio">{profile.bio}</p>}

      {profile.showcase.length > 0 && (
        <section className="profile-showcases">
          {profile.showcase.map((s, i) => (
            <div key={i} className="profile-showcase">
              <span className="profile-showcase-kind">{String(s.kind)}</span>
              <span className="profile-showcase-label">{String(s.label ?? s.value ?? '')}</span>
            </div>
          ))}
        </section>
      )}

      <ProfileComments
        handle={profile.handle ?? handle}
        viewerAccountId={viewerAccountId}
        isOwner={viewerAccountId != null && viewerAccountId === profile.account_id}
      />
    </div>
  );
}
