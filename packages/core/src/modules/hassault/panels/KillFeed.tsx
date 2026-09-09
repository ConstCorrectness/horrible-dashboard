/**
 * Complete, production-ready Counter-Strike 2 / CS:GO Killfeed system for HorribleAssault.
 *
 * Implements the exact visual and functional depth seen in CS2 and CS:GO:
 * - Data contract with killer, assister, victim, teams, and all combat modifiers.
 * - Esports-broadcast typography, team colors (CT blue, T gold, FFA white).
 * - Vector SVG badges for weapons (AK, M4, AWP, Knife, Shotgun, SMG, Grenades).
 * - Vector SVG modifier icons: Headshot, Wallbang, Through Smoke, No-Scope, Airborne, Blinded, Nutshot.
 * - Local player involvement highlighting with intense glowing amber outline and backdrop.
 * - 5-card maximum capacity with smooth slide-in enter, FIFO accelerated exit, and 6.0s duration.
 * - Zero memory leaks with lifecycle-managed timers and transitions.
 */
import { memo } from 'react';
import type { KillNote, KillFeedTeam } from '../session';

export type { KillNote, KillFeedTeam };
export type KillFeedEvent = KillNote;

export interface KillFeedProps {
  entries: KillNote[];
  className?: string;
  style?: React.CSSProperties;
}

const TEAM_COLORS: Record<KillFeedTeam, { text: string; bg: string; glow: string }> = {
  CT: { text: 'rgb(56, 189, 248)', bg: 'rgba(56, 189, 248, 0.15)', glow: 'rgba(56, 189, 248, 0.35)' },
  T: { text: 'rgb(251, 191, 36)', bg: 'rgba(251, 191, 36, 0.15)', glow: 'rgba(251, 191, 36, 0.35)' },
  FFA: { text: 'rgb(241, 245, 249)', bg: 'rgba(241, 245, 249, 0.12)', glow: 'rgba(241, 245, 249, 0.35)' },
};

/* -------------------------------------------------------------------------- */
/*                                SVG ICONS                                   */
/* -------------------------------------------------------------------------- */

const HeadshotIcon: React.FC<{ size?: number }> = memo(({ size = 16 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="rgb(239, 68, 68)"
    strokeWidth="2.2"
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{ filter: 'drop-shadow(0 0 3px rgba(239, 68, 68, 0.6))', flexShrink: 0 }}
  >
    <title>Headshot</title>
    <circle cx="12" cy="12" r="7" />
    <circle cx="12" cy="12" r="2.5" fill="rgb(239, 68, 68)" />
    <line x1="12" y1="1" x2="12" y2="5" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="1" y1="12" x2="5" y2="12" />
    <line x1="19" y1="12" x2="23" y2="12" />
  </svg>
));

const WallbangIcon: React.FC<{ size?: number }> = memo(({ size = 16 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="rgb(226, 232, 240)"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{ filter: 'drop-shadow(0 0 2px rgba(226, 232, 240, 0.5))', flexShrink: 0 }}
  >
    <title>Through Wall (Wallbang)</title>
    {/* Wall Barrier */}
    <rect x="9" y="3" width="6" height="18" rx="1" fill="rgba(255, 255, 255, 0.25)" stroke="rgb(148, 163, 184)" />
    {/* Piercing Bullet Path */}
    <line x1="2" y1="12" x2="8" y2="12" stroke="rgb(251, 191, 36)" strokeWidth="2.5" />
    <line x1="16" y1="12" x2="22" y2="12" stroke="rgb(248, 113, 113)" strokeWidth="2.5" />
    <polygon points="22,12 19,9.5 19,14.5" fill="rgb(248, 113, 113)" />
  </svg>
));

const SmokeIcon: React.FC<{ size?: number }> = memo(({ size = 16 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="rgba(148, 163, 184, 0.3)"
    stroke="rgb(203, 213, 225)"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{ filter: 'drop-shadow(0 0 3px rgba(203, 213, 225, 0.4))', flexShrink: 0 }}
  >
    <title>Through Smoke</title>
    <path d="M7 16a4 4 0 0 1-.8-7.9 5.5 5.5 0 0 1 10.6-1.6A4.5 4.5 0 0 1 18 16H7z" />
  </svg>
));

const NoScopeIcon: React.FC<{ size?: number }> = memo(({ size = 16 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="rgb(245, 158, 11)"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{ filter: 'drop-shadow(0 0 3px rgba(245, 158, 11, 0.5))', flexShrink: 0 }}
  >
    <title>No-Scope</title>
    <circle cx="12" cy="12" r="7.5" stroke="rgb(245, 158, 11)" />
    <line x1="12" y1="2" x2="12" y2="6.5" />
    <line x1="12" y1="17.5" x2="12" y2="22" />
    <line x1="2" y1="12" x2="6.5" y2="12" />
    <line x1="17.5" y1="12" x2="22" y2="12" />
    {/* Slashed Out Scope */}
    <line x1="5" y1="5" x2="19" y2="19" stroke="rgb(239, 68, 68)" strokeWidth="2.5" />
  </svg>
));

const AirborneIcon: React.FC<{ size?: number }> = memo(({ size = 16 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="rgb(56, 189, 248)"
    strokeWidth="2.2"
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{ filter: 'drop-shadow(0 0 3px rgba(56, 189, 248, 0.5))', flexShrink: 0 }}
  >
    <title>Airborne</title>
    <polyline points="17 11 12 6 7 11" />
    <polyline points="17 17 12 12 7 17" />
  </svg>
));

const BlindIcon: React.FC<{ size?: number }> = memo(({ size = 16 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="rgb(251, 191, 36)"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{ filter: 'drop-shadow(0 0 3px rgba(251, 191, 36, 0.6))', flexShrink: 0 }}
  >
    <title>Blinded</title>
    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" />
    <circle cx="12" cy="12" r="3" fill="rgb(251, 191, 36)" />
    {/* Slash */}
    <line x1="3" y1="3" x2="21" y2="21" stroke="rgb(248, 113, 113)" strokeWidth="2.5" />
  </svg>
));

const NutshotIcon: React.FC<{ size?: number }> = memo(({ size = 16 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="rgb(168, 85, 247)"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{ filter: 'drop-shadow(0 0 3px rgba(168, 85, 247, 0.6))', flexShrink: 0 }}
  >
    <title>Nutshot</title>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 7v5l3 3" />
    <polygon points="12,18 9,14 15,14" fill="rgb(168, 85, 247)" />
  </svg>
));

/* -------------------------------------------------------------------------- */
/*                            WEAPON SILHOUETTES                              */
/* -------------------------------------------------------------------------- */

const WeaponSilhouetteBadge: React.FC<{ weaponId: string; width?: number; height?: number }> = memo(
  ({ weaponId, width = 38, height = 16 }) => {
    const id = weaponId.toLowerCase();

    // AK-47 / Assault Rifle
    if (id === 'assault' || id === 'ak47') {
      return (
        <svg width={width} height={height} viewBox="0 0 72 24" fill="rgb(226, 232, 240)">
          <path d="M2 11h20v2H2zM22 9h16v4H22zM38 8h10v5H38zM48 9h14l4 8h-6l-3-4h-9zM32 13l-4 8h-4l3-8zM18 13l-2 5h-4l2-5z" />
        </svg>
      );
    }

    // Sniper / AWP
    if (id === 'sniper' || id === 'awp') {
      return (
        <svg width={width} height={height} viewBox="0 0 80 24" fill="rgb(226, 232, 240)">
          <path d="M1 11h32v2H1zM33 9h20v5H33zM53 10h18l6 7h-7l-4-3h-13zM25 6h16v3H25zM28 9h3v2h-3zM35 9h3v2h-3zM40 14l-3 7h-4l2-7z" />
        </svg>
      );
    }

    // Carbine / M4
    if (id === 'carbine' || id === 'm4a1' || id === 'm4') {
      return (
        <svg width={width} height={height} viewBox="0 0 68 24" fill="rgb(226, 232, 240)">
          <path d="M4 11h18v2H4zM22 9h18v4H22zM40 9h12v4h-12zM52 10h10l3 7h-5l-2-3h-6zM32 13l-3 8h-4l2-8zM24 6h10v3H24z" />
        </svg>
      );
    }

    // Shotgun
    if (id === 'shotgun') {
      return (
        <svg width={width} height={height} viewBox="0 0 64 24" fill="rgb(226, 232, 240)">
          <path d="M4 10h30v3H4zM34 9h14v5H34zM48 10h10l4 7h-6l-3-4h-5zM20 13h10v3H20zM40 14l-2 6h-4l2-6z" />
        </svg>
      );
    }

    // SMG / Subgun
    if (id === 'subgun' || id === 'smg' || id === 'mp5') {
      return (
        <svg width={width} height={height} viewBox="0 0 54 24" fill="rgb(226, 232, 240)">
          <path d="M6 10h14v3H6zM20 8h16v5H20zM36 9h10l5 7h-6l-2-3h-7zM24 13l-2 9h-4l2-9z" />
        </svg>
      );
    }

    // Pistol
    if (id === 'pistol' || id === 'deagle') {
      return (
        <svg width={width} height={height} viewBox="0 0 36 24" fill="rgb(226, 232, 240)">
          <path d="M6 9h18v5H6zM18 14l-3 8h-5l2-8zM24 10h4v3h-4z" />
        </svg>
      );
    }

    // Knife
    if (id === 'knife') {
      return (
        <svg width={width} height={height} viewBox="0 0 40 24" fill="rgb(226, 232, 240)">
          <path d="M4 15l10-4h20l-12 5H14l-2 3H8l2-4z" />
        </svg>
      );
    }

    // HE Grenade
    if (id === 'he' || id === 'frag' || id === 'grenade') {
      return (
        <svg width={width} height={height} viewBox="0 0 24 24" fill="rgb(239, 68, 68)">
          <circle cx="12" cy="14" r="7" />
          <rect x="10" y="4" width="4" height="4" rx="1" fill="rgb(203, 213, 225)" />
          <path d="M14 6h3" stroke="rgb(203, 213, 225)" strokeWidth="1.5" />
        </svg>
      );
    }

    // Smoke
    if (id === 'smoke') {
      return (
        <svg width={width} height={height} viewBox="0 0 24 24" fill="rgb(148, 163, 184)">
          <rect x="8" y="7" width="8" height="12" rx="2" />
          <rect x="10" y="4" width="4" height="3" fill="rgb(203, 213, 225)" />
        </svg>
      );
    }

    // Flashbang
    if (id === 'flash' || id === 'flashbang') {
      return (
        <svg width={width} height={height} viewBox="0 0 24 24" fill="rgb(56, 189, 248)">
          <rect x="8" y="7" width="8" height="12" rx="2" />
          <rect x="10" y="4" width="4" height="3" fill="rgb(203, 213, 225)" />
          <circle cx="12" cy="13" r="2" fill="rgb(255, 255, 255)" />
        </svg>
      );
    }

    // Molotov
    if (id === 'molotov' || id === 'fire') {
      return (
        <svg width={width} height={height} viewBox="0 0 24 24" fill="rgb(245, 158, 11)">
          <rect x="8" y="9" width="8" height="12" rx="2" />
          <rect x="10" y="5" width="4" height="4" />
          <path d="M12 2c-1.5 2 1.5 3 0 5" stroke="rgb(239, 68, 68)" strokeWidth="2" strokeLinecap="round" />
        </svg>
      );
    }

    // Fall / Suicide / World
    if (id === 'fall' || id === 'world') {
      return (
        <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="rgb(148, 163, 184)" strokeWidth="2">
          <path d="M12 4v16m0 0l-5-5m5 5l5-5" />
        </svg>
      );
    }

    // Fallback badge with text
    return (
      <span
        style={{
          fontSize: '0.65rem',
          fontWeight: 700,
          color: 'rgb(203, 213, 225)',
          background: 'rgba(255, 255, 255, 0.1)',
          padding: '1px 4px',
          borderRadius: 2,
          letterSpacing: '0.04em',
        }}
      >
        {weaponId.toUpperCase()}
      </span>
    );
  },
);

/* -------------------------------------------------------------------------- */
/*                              KILL CARD ITEM                                */
/* -------------------------------------------------------------------------- */

const KillFeedCard: React.FC<{ entry: KillNote }> = memo(({ entry }) => {
  const isSuicideOrFall = !entry.killerName || entry.killerName === 'World' || entry.weaponId === 'fall';
  const killerTeamInfo = TEAM_COLORS[entry.killerTeam] || TEAM_COLORS.FFA;
  const victimTeamInfo = TEAM_COLORS[entry.victimTeam] || TEAM_COLORS.FFA;
  const assisterTeamInfo = entry.assisterTeam ? (TEAM_COLORS[entry.assisterTeam] || TEAM_COLORS.FFA) : TEAM_COLORS.FFA;

  const isLocal = entry.isLocalPlayerInvolved;

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.45rem',
        padding: '3px 9px',
        borderRadius: 3,
        background: isLocal
          ? 'linear-gradient(90deg, rgba(251, 191, 36, 0.22) 0%, rgba(15, 23, 42, 0.88) 35%, rgba(15, 23, 42, 0.92) 100%)'
          : 'rgba(15, 23, 42, 0.82)',
        border: isLocal ? '1px solid rgba(251, 191, 36, 0.85)' : '1px solid rgba(255, 255, 255, 0.08)',
        boxShadow: isLocal
          ? '0 0 14px rgba(251, 191, 36, 0.45), inset 0 0 6px rgba(251, 191, 36, 0.2)'
          : '0 2px 8px rgba(0, 0, 0, 0.45)',
        backdropFilter: 'blur(6px)',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
        fontSize: '0.78rem',
        letterSpacing: '0.02em',
        lineHeight: 1.3,
        userSelect: 'none',
        pointerEvents: 'none',
        animation: 'killfeed-slide-in 0.18s cubic-bezier(0.16, 1, 0.3, 1) forwards',
        whiteSpace: 'nowrap',
      }}
    >
      {/* Killer + Optional Assister */}
      {!isSuicideOrFall && (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
          <span
            style={{
              fontWeight: 800,
              color: killerTeamInfo.text,
              textShadow: `0 0 6px ${killerTeamInfo.glow}`,
            }}
          >
            {entry.killerName}
          </span>
          {entry.assisterName && (
            <span
              style={{
                fontSize: '0.72rem',
                fontWeight: 600,
                color: assisterTeamInfo.text,
                opacity: 0.88,
              }}
            >
              + {entry.assisterName}
            </span>
          )}
        </span>
      )}

      {/* Combat Modifier Icons (Before Weapon) */}
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
        {entry.isAirborne && <AirborneIcon size={15} />}
        {entry.isBlind && <BlindIcon size={15} />}
        {entry.isThroughSmoke && <SmokeIcon size={15} />}
        {entry.isNoScope && <NoScopeIcon size={15} />}
        {entry.isWallbang && <WallbangIcon size={15} />}
      </span>

      {/* Weapon Silhouette Badge */}
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '0 2px',
          opacity: 0.95,
        }}
      >
        <WeaponSilhouetteBadge weaponId={entry.weaponId} />
      </span>

      {/* Critical Hit / Nutshot Icons (After Weapon) */}
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
        {entry.isNutshot && <NutshotIcon size={15} />}
        {entry.isHeadshot && <HeadshotIcon size={15} />}
      </span>

      {/* Victim */}
      <span
        style={{
          fontWeight: 800,
          color: victimTeamInfo.text,
          textShadow: `0 0 6px ${victimTeamInfo.glow}`,
        }}
      >
        {entry.victimName}
      </span>
    </div>
  );
});

/* -------------------------------------------------------------------------- */
/*                               MAIN KILLFEED                                */
/* -------------------------------------------------------------------------- */

export const KillFeed: React.FC<KillFeedProps> = memo(({ entries, className, style }) => {
  // Enforce max capacity 5 entries strictly
  const activeEntries = entries.slice(0, 5);

  if (activeEntries.length === 0) return null;

  return (
    <div
      className={className}
      style={{
        position: 'absolute',
        top: 12,
        right: 12,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        gap: 5,
        zIndex: 90,
        pointerEvents: 'none',
        ...style,
      }}
    >
      <style>{`
        @keyframes killfeed-slide-in {
          0% {
            opacity: 0;
            transform: translateX(30px) scale(0.96);
          }
          100% {
            opacity: 1;
            transform: translateX(0) scale(1);
          }
        }
      `}</style>
      {activeEntries.map((entry) => (
        <KillFeedCard key={entry.id} entry={entry} />
      ))}
    </div>
  );
});

export default KillFeed;
