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
import React, { memo, useState } from 'react';
import { ROUND_KILL_TTL_MS, type KillNote } from '../session';
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

/** Entrance length. A card older than this at mount is one being re-shown, not a new kill. */
const ENTER_MS = 260;
/** The deck fades over this much of the end of its life. */
const FADE_MS = 400;

/**
 * The deck's animations, aged off the kill timestamps rather than the mount.
 *
 * The HUD can remount (the Escape menu, a tab switch), and an animation keyed on
 * mount alone slid every card in again as though it were a new kill. A negative
 * delay puts a remounted deck at the point of its life it had reached.
 */
export function deckAnimation(
  cardTs: number,
  lastTs: number,
  now: number,
): { card: string; deck: string } {
  const cardAge = Math.max(0, now - cardTs);
  const fadeElapsed = Math.max(0, now - lastTs) - (ROUND_KILL_TTL_MS - FADE_MS);
  return {
    card:
      cardAge < ENTER_MS ? `killCardSlideUp ${ENTER_MS}ms cubic-bezier(0.16, 1, 0.3, 1)` : 'none',
    deck: `killDeckFade ${FADE_MS}ms linear ${-fadeElapsed}ms forwards`,
  };
}

const SingleKillCard: React.FC<{ card: KillNote; index: number }> = memo(({ card, index }) => {
  // Read once per card mount, so a later render does not restart or drop it.
  const [entrance] = useState(() => deckAnimation(card.ts, card.ts, Date.now()).card);
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
        animation: entrance,
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
  const lastTs = cards[cards.length - 1].ts;
  const { deck } = deckAnimation(lastTs, lastTs, Date.now());

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
        @keyframes killDeckFade {
          from { opacity: 1; }
          to { opacity: 0; }
        }
      `}</style>
      {/* Keyed on the latest kill, so a new kill restarts the fade clock. */}
      <div
        key={lastTs}
        className={className}
        style={{
          animation: deck,
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
