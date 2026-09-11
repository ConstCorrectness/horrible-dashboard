/**
 * Crossfire Kill Badges & Tactical Combat HUD.
 *
 * Implements Crossfire's iconic Kill Mark / Badge notification system:
 * - Animated center-screen pop-in with metallic sheen and particle pulse.
 * - Distinct badges: HEADSHOT (Flaming Golden Winged Skull), DOUBLE KILL,
 *   MULTI KILL, ULTRA KILL, UNSTOPPABLE, KNIFE KILL, WALLBANG, and STANDARD KILL.
 * - Red Crossfire 4-notch hitmarker confirmation around the crosshair.
 * - Crossfire Global Risk vs Black List (GR / BL) Tactical Score Header with Bomb Site A/B status.
 */
import React, { useEffect, useState } from 'react';

export type KillBadgeType =
  | 'headshot'
  | 'double'
  | 'triple'
  | 'quad'
  | 'unstoppable'
  | 'knife'
  | 'grenade'
  | 'wallbang'
  | 'kill';

export interface KillBadgeData {
  id: number;
  type: KillBadgeType;
  timestamp: number;
  streak: number;
}

interface CrossfireKillBadgesProps {
  badge: KillBadgeData | null;
  hit: boolean;
  headHit?: boolean;
}

export const CrossfireKillBadges: React.FC<CrossfireKillBadgesProps> = ({
  badge,
  hit,
  headHit,
}) => {
  const [activeBadge, setActiveBadge] = useState<KillBadgeData | null>(badge);
  const [animKey, setAnimKey] = useState(0);

  useEffect(() => {
    if (badge) {
      setActiveBadge(badge);
      setAnimKey((k) => k + 1);
      const timer = setTimeout(() => {
        setActiveBadge(null);
      }, 1900);
      return () => clearTimeout(timer);
    }
  }, [badge]);

  return (
    <>
      {/* 1. Tactical Crossfire Hitmarker: 4 angled red notches around crosshair */}
      {hit && (
        <div
          style={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            width: 28,
            height: 28,
            transform: 'translate(-50%, -50%)',
            pointerEvents: 'none',
            zIndex: 60,
          }}
        >
          <svg
            width="28"
            height="28"
            viewBox="0 0 28 28"
            style={{
              filter: headHit
                ? 'drop-shadow(0 0 6px rgba(255, 215, 0, 0.9))'
                : 'drop-shadow(0 0 4px rgba(255, 50, 50, 0.8))',
            }}
          >
            {/* Top-Left notch */}
            <line
              x1="6"
              y1="6"
              x2="10"
              y2="10"
              stroke={headHit ? 'rgb(255, 215, 0)' : 'rgb(255, 34, 34)'}
              strokeWidth={headHit ? '2.5' : '2'}
              strokeLinecap="round"
            />
            {/* Top-Right notch */}
            <line
              x1="22"
              y1="6"
              x2="18"
              y2="10"
              stroke={headHit ? 'rgb(255, 215, 0)' : 'rgb(255, 34, 34)'}
              strokeWidth={headHit ? '2.5' : '2'}
              strokeLinecap="round"
            />
            {/* Bottom-Left notch */}
            <line
              x1="6"
              y1="22"
              x2="10"
              y2="18"
              stroke={headHit ? 'rgb(255, 215, 0)' : 'rgb(255, 34, 34)'}
              strokeWidth={headHit ? '2.5' : '2'}
              strokeLinecap="round"
            />
            {/* Bottom-Right notch */}
            <line
              x1="22"
              y1="22"
              x2="18"
              y2="18"
              stroke={headHit ? 'rgb(255, 215, 0)' : 'rgb(255, 34, 34)'}
              strokeWidth={headHit ? '2.5' : '2'}
              strokeLinecap="round"
            />
          </svg>
        </div>
      )}

      {/* 2. Crossfire Kill Badge Popup: Center-screen below crosshair */}
      {activeBadge && (
        <div
          key={animKey}
          style={{
            position: 'absolute',
            left: '50%',
            top: '62%',
            transform: 'translate(-50%, -50%)',
            pointerEvents: 'none',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            zIndex: 70,
            animation: 'cfBadgePop 1.8s cubic-bezier(0.18, 0.89, 0.32, 1.28) forwards',
          }}
        >
          <style>{`
            @keyframes cfBadgePop {
              0% {
                transform: translate(-50%, -50%) scale(1.6);
                opacity: 0;
              }
              12% {
                transform: translate(-50%, -50%) scale(1.0);
                opacity: 1;
              }
              75% {
                transform: translate(-50%, -50%) scale(1.0);
                opacity: 1;
              }
              100% {
                transform: translate(-50%, -70%) scale(0.92);
                opacity: 0;
              }
            }
            @keyframes cfGlowPulse {
              0%, 100% { opacity: 0.6; transform: scale(1.0); }
              50% { opacity: 1.0; transform: scale(1.08); }
            }
          `}</style>

          {/* Golden Badge Glow Background */}
          <div
            style={{
              position: 'absolute',
              width: 170,
              height: 170,
              borderRadius: '50%',
              background:
                activeBadge.type === 'headshot' || activeBadge.type === 'unstoppable'
                  ? 'radial-gradient(circle, rgba(255,185,0,0.45) 0%, rgba(255,80,0,0.15) 55%, transparent 75%)'
                  : 'radial-gradient(circle, rgba(200,225,255,0.35) 0%, rgba(50,120,255,0.1) 55%, transparent 75%)',
              animation: 'cfGlowPulse 1.2s infinite ease-in-out',
              zIndex: -1,
            }}
          />

          {/* SVG Badge Graphics */}
          <BadgeGraphic type={activeBadge.type} streak={activeBadge.streak} />

          {/* Metallic Plaque / Banner */}
          <BadgeBanner type={activeBadge.type} streak={activeBadge.streak} />
        </div>
      )}
    </>
  );
};

const BadgeGraphic: React.FC<{ type: KillBadgeType; streak: number }> = ({ type }) => {
  const isGold =
    type === 'headshot' ||
    type === 'double' ||
    type === 'triple' ||
    type === 'quad' ||
    type === 'unstoppable' ||
    type === 'knife';

  return (
    <svg width="150" height="95" viewBox="0 0 150 95" style={{ overflow: 'visible' }}>
      <defs>
        {/* Gold Metal Gradient */}
        <linearGradient id="goldGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="rgb(255, 242, 168)" />
          <stop offset="25%" stopColor="rgb(229, 168, 35)" />
          <stop offset="60%" stopColor="rgb(150, 97, 7)" />
          <stop offset="85%" stopColor="rgb(245, 202, 69)" />
          <stop offset="100%" stopColor="rgb(255, 253, 224)" />
        </linearGradient>

        {/* Silver Metal Gradient */}
        <linearGradient id="silverGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="rgb(255, 255, 255)" />
          <stop offset="30%" stopColor="rgb(184, 194, 204)" />
          <stop offset="70%" stopColor="rgb(96, 111, 123)" />
          <stop offset="100%" stopColor="rgb(220, 227, 232)" />
        </linearGradient>

        {/* Flame Gradient for Headshot */}
        <linearGradient id="flameGrad" x1="0%" y1="100%" x2="0%" y2="0%">
          <stop offset="0%" stopColor="rgb(255, 51, 0)" />
          <stop offset="50%" stopColor="rgb(255, 153, 0)" />
          <stop offset="100%" stopColor="rgb(255, 238, 85)" />
        </linearGradient>
      </defs>

      {/* Wings Behind Skull */}
      <g fill={isGold ? 'url(#goldGrad)' : 'url(#silverGrad)'} stroke="rgb(26, 21, 5)" strokeWidth="1">
        {/* Left Wing */}
        <path d="M 70 45 C 50 30, 20 20, 5 35 C 20 48, 45 55, 68 58 Z" />
        <path d="M 68 55 C 45 42, 18 35, 10 50 C 25 58, 50 64, 66 66 Z" />
        <path d="M 66 63 C 45 55, 25 50, 18 62 C 32 68, 52 70, 65 72 Z" />

        {/* Right Wing */}
        <path d="M 80 45 C 100 30, 130 20, 145 35 C 130 48, 105 55, 82 58 Z" />
        <path d="M 82 55 C 105 42, 132 35, 140 50 C 125 58, 100 64, 84 66 Z" />
        <path d="M 84 63 C 105 55, 125 50, 132 62 C 118 68, 98 70, 85 72 Z" />
      </g>

      {/* Headshot Flame Crown */}
      {type === 'headshot' && (
        <path
          d="M 55 26 Q 62 8, 68 20 Q 75 2, 82 20 Q 88 8, 95 26 C 85 30, 65 30, 55 26 Z"
          fill="url(#flameGrad)"
          stroke="rgb(255, 34, 0)"
          strokeWidth="0.75"
        />
      )}

      {/* Central Skull Head */}
      <g transform="translate(47, 18)">
        {/* Skull Cranium */}
        <path
          d="M 12 18 C 12 8, 44 8, 44 18 C 46 28, 42 34, 38 38 L 38 48 C 38 51, 18 51, 18 48 L 18 38 C 14 34, 10 28, 12 18 Z"
          fill={isGold ? 'url(#goldGrad)' : 'url(#silverGrad)'}
          stroke="rgb(17, 17, 17)"
          strokeWidth="1.2"
        />

        {/* Glowing Eye Sockets */}
        <ellipse
          cx="22"
          cy="26"
          rx="4.5"
          ry="6"
          fill={type === 'headshot' ? 'rgb(255, 0, 51)' : 'rgb(17, 17, 17)'}
          stroke="rgb(0, 0, 0)"
          strokeWidth="1"
        />
        <ellipse
          cx="34"
          cy="26"
          rx="4.5"
          ry="6"
          fill={type === 'headshot' ? 'rgb(255, 0, 51)' : 'rgb(17, 17, 17)'}
          stroke="rgb(0, 0, 0)"
          strokeWidth="1"
        />
        {type === 'headshot' && (
          <>
            <circle cx="22" cy="26" r="2" fill="rgb(255, 255, 136)" />
            <circle cx="34" cy="26" r="2" fill="rgb(255, 255, 136)" />
          </>
        )}

        {/* Inverted Heart Nasal Cavity */}
        <path d="M 28 32 L 26 36 L 30 36 Z" fill="rgb(17, 17, 17)" />

        {/* Teeth / Jaw Rows */}
        <line x1="22" y1="44" x2="22" y2="48" stroke="rgb(17, 17, 17)" strokeWidth="1.2" />
        <line x1="28" y1="44" x2="28" y2="48" stroke="rgb(17, 17, 17)" strokeWidth="1.2" />
        <line x1="34" y1="44" x2="34" y2="48" stroke="rgb(17, 17, 17)" strokeWidth="1.2" />
      </g>

      {/* Knife Kill Crossed Blades Overlay */}
      {type === 'knife' && (
        <g stroke="rgb(255, 255, 255)" strokeWidth="2.5" strokeLinecap="round" filter="drop-shadow(0 0 3px rgb(255, 170, 0))">
          <line x1="35" y1="20" x2="115" y2="75" />
          <line x1="115" y1="20" x2="35" y2="75" />
        </g>
      )}

      {/* Wallbang Penetration Bullet Icon */}
      {type === 'wallbang' && (
        <g transform="translate(62, 58)" filter="drop-shadow(0 0 3px rgb(255, 51, 51))">
          <rect x="-18" y="-12" width="6" height="24" fill="rgb(102, 102, 102)" stroke="rgb(34, 34, 34)" />
          <polygon points="12,0 0,-6 0,6" fill="rgb(255, 215, 0)" stroke="rgb(255, 170, 0)" />
        </g>
      )}
    </svg>
  );
};

const BadgeBanner: React.FC<{ type: KillBadgeType; streak: number }> = ({ type }) => {
  const getBannerText = () => {
    switch (type) {
      case 'headshot':
        return 'HEADSHOT!';
      case 'double':
        return 'DOUBLE KILL';
      case 'triple':
        return 'MULTI KILL';
      case 'quad':
        return 'ULTRA KILL';
      case 'unstoppable':
        return 'UNSTOPPABLE!';
      case 'knife':
        return 'KNIFE KILL';
      case 'wallbang':
        return 'WALLBANG!';
      case 'grenade':
        return 'GRENADE KILL';
      default:
        return 'KILL';
    }
  };

  const isGold = type !== 'kill';

  return (
    <div
      style={{
        marginTop: -6,
        padding: '3px 18px',
        borderRadius: 3,
        background: isGold
          ? 'linear-gradient(180deg, rgb(255, 215, 0) 0%, rgb(184, 134, 11) 60%, rgb(90, 64, 4) 100%)'
          : 'linear-gradient(180deg, rgb(224, 230, 237) 0%, rgb(138, 155, 168) 60%, rgb(62, 76, 89) 100%)',
        border: isGold ? '1.5px solid rgb(255, 245, 165)' : '1.5px solid rgb(255, 255, 255)',
        boxShadow: isGold
          ? '0 3px 10px rgba(255, 180, 0, 0.75), inset 0 1px 1px rgba(255,255,255,0.8)'
          : '0 3px 8px rgba(0, 0, 0, 0.6), inset 0 1px 1px rgba(255,255,255,0.8)',
      }}
    >
      <span
        style={{
          fontFamily: "'Impact', 'Arial Black', sans-serif",
          fontSize: '1.05rem',
          letterSpacing: '0.12em',
          fontWeight: 900,
          color: 'rgb(26, 13, 0)',
          textShadow: '0 1px 0 rgba(255,255,255,0.6)',
          textTransform: 'uppercase',
        }}
      >
        {getBannerText()}
      </span>
    </div>
  );
};

/**
 * Crossfire Tactical Match Header:
 * Displays Global Risk (GR) vs Black List (BL) round score and bomb site A/B status.
 */
interface CrossfireHeaderHUDProps {
  scoreGR: number;
  scoreBL: number;
  roundTimerSeconds?: number;
  bombPlantedSite?: 'A' | 'B' | null;
  myTeam: number; // 0 = BL (Terrorist/CLA), 1 = GR (Counter-Terrorist/RVSF)
}

export const CrossfireHeaderHUD: React.FC<CrossfireHeaderHUDProps> = ({
  scoreGR,
  scoreBL,
  roundTimerSeconds = 120,
  bombPlantedSite = null,
  myTeam,
}) => {
  const minutes = Math.floor(roundTimerSeconds / 60);
  const seconds = roundTimerSeconds % 60;
  const timeStr = `${minutes}:${seconds.toString().padStart(2, '0')}`;

  return (
    <div
      style={{
        position: 'absolute',
        top: 6,
        left: '50%',
        transform: 'translateX(-50%)',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '3px 12px',
        background: 'linear-gradient(180deg, rgba(16,22,30,0.92) 0%, rgba(8,12,18,0.95) 100%)',
        border: '1px solid rgba(255,255,255,0.18)',
        borderRadius: 4,
        boxShadow: '0 4px 14px rgba(0,0,0,0.65)',
        fontFamily: "'Trebuchet MS', 'Arial', sans-serif",
        fontSize: '0.8rem',
        color: 'rgb(255, 255, 255)',
        zIndex: 45,
        pointerEvents: 'none',
      }}
    >
      {/* Global Risk (GR, Blue) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span
          style={{
            background: 'linear-gradient(180deg, rgb(61, 136, 224) 0%, rgb(26, 78, 138) 100%)',
            padding: '1px 6px',
            borderRadius: 2,
            fontWeight: 800,
            fontSize: '0.72rem',
            letterSpacing: '0.05em',
            border: myTeam === 1 ? '1px solid rgb(115, 179, 255)' : 'none',
          }}
        >
          GLOBAL RISK
        </span>
        <strong style={{ fontSize: '1.15rem', color: 'rgb(104, 173, 255)' }}>{scoreGR}</strong>
      </div>

      {/* Center Timer & Bomb Indicator */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          minWidth: 55,
          padding: '0 6px',
          borderLeft: '1px solid rgba(255,255,255,0.15)',
          borderRight: '1px solid rgba(255,255,255,0.15)',
        }}
      >
        <span
          style={{
            fontFamily: 'monospace',
            fontWeight: 700,
            fontSize: '0.92rem',
            color: bombPlantedSite ? 'rgb(255, 59, 48)' : 'rgb(255, 255, 255)',
          }}
        >
          {timeStr}
        </span>
        <div style={{ display: 'flex', gap: 4, marginTop: 1 }}>
          <span
            style={{
              padding: '0 3px',
              borderRadius: 2,
              fontSize: '0.62rem',
              fontWeight: 800,
              background: bombPlantedSite === 'A' ? 'rgb(255, 59, 48)' : 'rgba(255,255,255,0.15)',
              color: bombPlantedSite === 'A' ? 'rgb(255, 255, 255)' : 'rgb(170, 170, 170)',
              animation: bombPlantedSite === 'A' ? 'cfBombBlink 0.6s infinite' : 'none',
            }}
          >
            [A]
          </span>
          <span
            style={{
              padding: '0 3px',
              borderRadius: 2,
              fontSize: '0.62rem',
              fontWeight: 800,
              background: bombPlantedSite === 'B' ? 'rgb(255, 59, 48)' : 'rgba(255,255,255,0.15)',
              color: bombPlantedSite === 'B' ? 'rgb(255, 255, 255)' : 'rgb(170, 170, 170)',
              animation: bombPlantedSite === 'B' ? 'cfBombBlink 0.6s infinite' : 'none',
            }}
          >
            [B]
          </span>
        </div>
      </div>

      {/* Black List (BL, Orange) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <strong style={{ fontSize: '1.15rem', color: 'rgb(255, 157, 66)' }}>{scoreBL}</strong>
        <span
          style={{
            background: 'linear-gradient(180deg, rgb(224, 108, 40) 0%, rgb(138, 59, 14) 100%)',
            padding: '1px 6px',
            borderRadius: 2,
            fontWeight: 800,
            fontSize: '0.72rem',
            letterSpacing: '0.05em',
            border: myTeam === 0 ? '1px solid rgb(255, 178, 122)' : 'none',
          }}
        >
          BLACK LIST
        </span>
      </div>

      <style>{`
        @keyframes cfBombBlink {
          0%, 100% { opacity: 1.0; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
};
