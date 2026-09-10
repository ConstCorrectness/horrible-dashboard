/**
 * Counter-Strike 2 Bottom-Center Kill Card Deck.
 *
 * Renders a row of tactical cards at the bottom center of the player's view,
 * tracking their consecutive kills in the active round/life.
 *
 * Displays:
 * - Kill counter / tier badge (I, II, III, IV, ACE)
 * - Victim name colored by victim team
 * - Weapon silhouette
 * - Modifiers: Headshot, Wallbang, Smoke, NoScope, Airborne, Blind, Nutshot, Backstab
 * - Glowing border and backdrop escalating from Bronze -> Silver -> Gold -> Ace Prismatic
 */
import React, { memo } from 'react';
import type { KillNote } from '../session';
import {
  TEAM_COLORS,
  HeadshotIcon,
  WallbangIcon,
  SmokeIcon,
  NoScopeIcon,
  AirborneIcon,
  BlindIcon,
  NutshotIcon,
  WeaponSilhouetteBadge,
} from './KillFeed';

export interface KillCardDeckProps {
  cards: KillNote[];
  className?: string;
  style?: React.CSSProperties;
}

const ROMAN_NUMERALS = ['I', 'II', 'III', 'IV', 'ACE', 'VI', 'VII'];

const TIER_ACCENTS = [
  { border: 'rgba(148, 163, 184, 0.5)', glow: 'rgba(148, 163, 184, 0.2)', tag: 'KILL 1' },
  { border: 'rgba(56, 189, 248, 0.7)', glow: 'rgba(56, 189, 248, 0.35)', tag: 'DOUBLE' },
  { border: 'rgba(251, 191, 36, 0.85)', glow: 'rgba(251, 191, 36, 0.45)', tag: 'TRIPLE' },
  { border: 'rgba(249, 115, 22, 0.9)', glow: 'rgba(249, 115, 22, 0.55)', tag: 'QUAD' },
  { border: 'rgba(239, 68, 68, 1.0)', glow: 'rgba(239, 68, 68, 0.65)', tag: 'ACE!' },
];

const SingleKillCard: React.FC<{ card: KillNote; index: number }> = memo(({ card, index }) => {
  const tier = TIER_ACCENTS[Math.min(index, TIER_ACCENTS.length - 1)];
  const victimColor = TEAM_COLORS[card.victimTeam]?.text ?? 'rgb(241, 245, 249)';
  const roman = ROMAN_NUMERALS[Math.min(index, ROMAN_NUMERALS.length - 1)];

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '6px 10px',
        minWidth: '96px',
        maxWidth: '128px',
        background: 'rgba(15, 23, 42, 0.88)',
        backdropFilter: 'blur(8px)',
        border: `1.5px solid ${tier.border}`,
        borderRadius: '6px',
        boxShadow: `0 4px 14px rgba(0, 0, 0, 0.6), 0 0 10px ${tier.glow}`,
        animation: 'killCardSlideUp 260ms cubic-bezier(0.16, 1, 0.3, 1)',
        position: 'relative',
        userSelect: 'none',
      }}
    >
      {/* Top Banner: Rank/Tier tag */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          width: '100%',
          borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
          paddingBottom: '3px',
          marginBottom: '5px',
        }}
      >
        <span
          style={{
            fontFamily: 'monospace',
            fontWeight: 800,
            fontSize: '11px',
            color: tier.border,
            letterSpacing: '0.08em',
          }}
        >
          {roman}
        </span>
        <span
          style={{
            fontFamily: 'system-ui, sans-serif',
            fontSize: '9px',
            fontWeight: 700,
            color: 'rgba(255, 255, 255, 0.6)',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          {tier.tag}
        </span>
      </div>

      {/* Victim Name */}
      <div
        style={{
          fontFamily: 'system-ui, sans-serif',
          fontSize: '11px',
          fontWeight: 700,
          color: victimColor,
          maxWidth: '100%',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          marginBottom: '4px',
          textShadow: `0 0 4px ${victimColor}`,
        }}
        title={card.victimName}
      >
        {card.victimName}
      </div>

      {/* Weapon Silhouette */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '20px',
          margin: '2px 0 4px',
        }}
      >
        <WeaponSilhouetteBadge weaponId={card.weaponId} width={42} height={16} />
      </div>

      {/* Modifier Badges */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '3px',
          minHeight: '16px',
        }}
      >
        {card.isHeadshot && <HeadshotIcon size={14} />}
        {card.isWallbang && <WallbangIcon size={14} />}
        {card.isThroughSmoke && <SmokeIcon size={14} />}
        {card.isNoScope && <NoScopeIcon size={14} />}
        {card.isAirborne && <AirborneIcon size={14} />}
        {card.isBlind && <BlindIcon size={14} />}
        {card.isNutshot && <NutshotIcon size={14} />}
        {card.isBackstab && (
          <span
            style={{
              fontSize: '13px',
              lineHeight: 1,
              filter: 'drop-shadow(0 0 3px rgba(239, 68, 68, 0.7))',
            }}
            title="Backstab"
          >
            🗡️
          </span>
        )}
      </div>
    </div>
  );
});

export const KillCardDeck: React.FC<KillCardDeckProps> = memo(({ cards, className, style }) => {
  if (!cards || cards.length === 0) return null;

  return (
    <>
      <style>{`
        @keyframes killCardSlideUp {
          0% {
            opacity: 0;
            transform: translateY(18px) scale(0.9);
          }
          100% {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
      `}</style>
      <div
        className={className}
        style={{
          display: 'flex',
          flexDirection: 'row',
          alignItems: 'flex-end',
          justifyContent: 'center',
          gap: '8px',
          pointerEvents: 'none',
          ...style,
        }}
      >
        {cards.map((card, idx) => (
          <SingleKillCard key={card.id} card={card} index={idx} />
        ))}
      </div>
    </>
  );
});

export default KillCardDeck;
