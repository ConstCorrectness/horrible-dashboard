/**
 * The weapon a skin is painted on, drawn flat.
 *
 * This is the armoury's product shot: the card in the inventory, the catalogue
 * tile, and the inspect viewport all render the same component at three sizes.
 * It is the only place a player sees a skin *before* deciding to equip it, so
 * the three numbers the economy is built on have to be visible here or they are
 * not visible anywhere:
 *
 * - **`floatValue`** is wear, and wear is scratches and scuffs — not a slightly
 *   lower opacity. A float nobody can see is a number the whole trade-up
 *   contract is arithmetic over and no player can check.
 * - **`patternSeed`** is what makes two copies of one skin different items. It
 *   seeds the finish's angle, the pattern's phase, and where the scratches fall,
 *   so seed #4 and seed #822 are visibly not the same gun. Previously it only
 *   went into an element id, which changed nothing on screen.
 * - **`patternType`** picks the overlay — the pattern `<defs>` that already
 *   existed here was never referenced by any shape, so a Camo, a Fade and a
 *   Solid all drew identically.
 *
 * Procedural like the rest of the module: no bitmaps, no borrowed art. The parts
 * are paths, the finish is a gradient, and the pattern is an SVG `<pattern>`.
 */
import type { CSSProperties } from 'react';

/**
 * Weapon furniture — the parts a skin does *not* paint.
 *
 * Deliberately fixed rather than themed: polymer grips and blued barrels are
 * this weapon's materials, and a stock that turned white under the light theme
 * would read as a different gun rather than as a themed panel. The painted
 * surfaces all take the skin instead.
 */
const POLYMER = '#18181b';
const STEEL = '#3f3f46';
const BORE = '#09090b';

export interface WeaponRenderProps {
  weaponId: string;
  baseColor: string;
  accentColor: string;
  patternType: string;
  patternSeed?: number;
  floatValue?: number;
  isInspecting?: boolean;
  style?: CSSProperties;
  className?: string;
}

/**
 * A small deterministic generator, seeded by the pattern seed.
 *
 * Deterministic is the whole requirement: an item's scuffs must be *that item's*
 * scuffs on every render, or the inventory would reshuffle its own wear every
 * time React re-rendered the grid.
 */
function rng(seed: number): () => number {
  let state = (seed || 1) * 1103515245 + 12345;
  return () => {
    state = (state * 1103515245 + 12345) & 0x7fffffff;
    return state / 0x7fffffff;
  };
}

/**
 * The scuffs and scratches for a float value.
 *
 * Factory New (< 0.07) gets nothing at all — that is what Factory New *means* —
 * and a Battle-Scarred gun gets a couple of dozen, concentrated toward the edges
 * of the painted area where a real one wears first.
 */
function Wear({ seed, floatValue }: { seed: number; floatValue: number }) {
  if (floatValue < 0.07) return null;
  const next = rng(seed);
  const count = Math.round(Math.min(1, floatValue) * 26);
  const marks = [];
  for (let i = 0; i < count; i += 1) {
    const x = 20 + next() * 200;
    const y = 30 + next() * 40;
    const len = 3 + next() * 11;
    const drop = (next() - 0.5) * 4;
    marks.push(
      <line
        key={i}
        x1={x}
        y1={y}
        x2={x + len}
        y2={y + drop}
        stroke={next() > 0.5 ? '#000000' : '#ffffff'}
        strokeWidth={0.6 + next() * 0.8}
        opacity={0.1 + Math.min(0.45, floatValue * 0.5)}
        strokeLinecap="round"
      />,
    );
  }
  return <g style={{ mixBlendMode: 'overlay' }}>{marks}</g>;
}

export function WeaponSilhouette({
  weaponId,
  baseColor,
  accentColor,
  patternType,
  patternSeed = 100,
  floatValue = 0.05,
  isInspecting = false,
  style,
  className,
}: WeaponRenderProps) {
  const uid = `${weaponId}-${baseColor.replace('#', '')}-${accentColor.replace('#', '')}-${patternSeed}`;
  const finishId = `wep-finish-${uid}`;
  const patternId = `wep-pattern-${uid}`;
  const glossId = `wep-gloss-${uid}`;

  // The seed rotates the finish and phases the pattern, which is what makes one
  // instance of a skin distinguishable from another at a glance.
  const angle = (patternSeed % 360) - 180;
  const phase = patternSeed % 20;

  // Wear dulls the paint as well as scratching it. Floored well above zero: a
  // Battle-Scarred skin should look tired, not absent.
  const wearOpacity = Math.max(0.45, 1 - floatValue * 0.55);

  const inspectTransform = isInspecting
    ? 'rotate(-12deg) scale(1.08) translateY(-6px)'
    : 'rotate(0deg) scale(1) translateY(0px)';

  /** Every painted surface takes this: the finish, then the pattern over it. */
  const paint = `url(#${finishId})`;

  return (
    <div
      className={className}
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        // Ease-out-quint, not an overshoot: the inspect pose turns a weapon over,
        // and a spring at the end of it reads as a UI animation rather than as
        // an object being handled.
        transition: 'transform 0.4s cubic-bezier(0.22, 1, 0.36, 1)',
        transform: inspectTransform,
        filter: 'drop-shadow(0 8px 16px rgba(0,0,0,0.6))',
        ...style,
      }}
    >
      <svg
        viewBox="0 0 240 100"
        style={{ width: '100%', height: '100%', maxHeight: 120, overflow: 'visible' }}
      >
        <defs>
          <linearGradient id={finishId} gradientTransform={`rotate(${angle} 0.5 0.5)`}>
            {patternType === 'fade' ? (
              // A fade is a fade *across the weapon*, so the two colours meet
              // once rather than repeating.
              <>
                <stop offset="0%" stopColor={baseColor} stopOpacity={wearOpacity} />
                <stop offset="55%" stopColor={accentColor} stopOpacity={wearOpacity} />
                <stop offset="100%" stopColor={baseColor} stopOpacity={wearOpacity * 0.75} />
              </>
            ) : (
              <>
                <stop offset="0%" stopColor={baseColor} stopOpacity={wearOpacity} />
                <stop offset="48%" stopColor={baseColor} stopOpacity={wearOpacity} />
                <stop offset="52%" stopColor={accentColor} stopOpacity={wearOpacity * 0.9} />
                <stop offset="100%" stopColor={baseColor} stopOpacity={wearOpacity * 0.85} />
              </>
            )}
          </linearGradient>

          {/* A single soft highlight along the top of every part, so a flat fill
              reads as a rounded surface rather than as a sticker. */}
          <linearGradient id={glossId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.28" />
            <stop offset="45%" stopColor="#ffffff" stopOpacity="0.04" />
            <stop offset="100%" stopColor="#000000" stopOpacity="0.35" />
          </linearGradient>

          {/* The pattern overlay. This existed before and was never referenced by
              a single shape, so every pattern type drew the same gun. */}
          <pattern
            id={patternId}
            width="20"
            height="20"
            patternUnits="userSpaceOnUse"
            patternTransform={`translate(${phase} ${phase / 2}) rotate(${patternType === 'camo' ? 0 : 12})`}
          >
            {patternType === 'camo' ? (
              <>
                <path d="M0,0 L11,6 L6,16 L0,12 Z" fill={accentColor} opacity="0.35" />
                <path d="M13,9 L20,4 L20,17 L14,18 Z" fill={accentColor} opacity="0.22" />
              </>
            ) : patternType === 'patina' ? (
              <>
                <circle cx="6" cy="6" r="5.5" fill={accentColor} opacity="0.3" />
                <circle cx="15" cy="15" r="3.5" fill={accentColor} opacity="0.2" />
              </>
            ) : patternType === 'custom_art' ? (
              <>
                <path
                  d="M0,10 Q5,2 10,10 T20,10"
                  fill="none"
                  stroke={accentColor}
                  strokeWidth="1.6"
                  opacity="0.45"
                />
                <circle cx="10" cy="17" r="1.6" fill={accentColor} opacity="0.5" />
              </>
            ) : patternType === 'anodized' ? (
              <line
                x1="0"
                y1="0"
                x2="0"
                y2="20"
                stroke={accentColor}
                strokeWidth="2.5"
                opacity="0.22"
              />
            ) : patternType === 'fade' ? (
              // A fade is carried by the gradient; a repeating overlay on top of
              // it would be a second pattern nobody asked for.
              <g />
            ) : (
              <line
                x1="0"
                y1="20"
                x2="20"
                y2="0"
                stroke={accentColor}
                strokeWidth="1.2"
                opacity="0.18"
              />
            )}
          </pattern>
        </defs>

        {/* KNIFE */}
        {weaponId === 'knife' && (
          <g>
            {/* Blade, with a ground bevel and a swage line along the spine. */}
            <path
              d="M 62,50 Q 140,24 212,34 Q 214,40 206,44 Q 160,64 108,61 L 62,61 Z"
              fill={paint}
              stroke="rgba(255,255,255,0.35)"
              strokeWidth="1.2"
            />
            <path
              d="M 62,50 Q 140,24 212,34 Q 214,40 206,44 Q 160,64 108,61 L 62,61 Z"
              fill={`url(#${patternId})`}
            />
            <path
              d="M 62,50 Q 140,24 212,34 Q 214,40 206,44 Q 160,64 108,61 L 62,61 Z"
              fill={`url(#${glossId})`}
            />
            <path
              d="M 74,53 Q 138,33 198,40"
              stroke="rgba(255,255,255,0.65)"
              strokeWidth="1.1"
              fill="none"
            />
            {/* Serrations on the spine, near the guard. */}
            <path
              d="M 78,44 l 5,-3 l 1,4 l 5,-3 l 1,4 l 5,-3"
              fill="none"
              stroke={STEEL}
              strokeWidth="1.2"
            />
            {/* Guard, handle scales and pommel — furniture, not painted. */}
            <path d="M 56,42 L 62,42 L 62,66 L 56,66 Z" fill={STEEL} />
            <path
              d="M 20,47 L 56,47 L 55,63 L 19,63 Z"
              fill={POLYMER}
              stroke={STEEL}
              strokeWidth="1"
            />
            <path d="M 24,50 L 52,50 L 51,54 L 23,54 Z" fill="rgba(255,255,255,0.06)" />
            <circle cx="26" cy="57" r="2.4" fill={STEEL} />
            <circle cx="42" cy="57" r="2.4" fill={STEEL} />
            <path d="M 14,46 L 20,46 L 20,64 L 14,63 Z" fill={STEEL} />
            <circle cx="17" cy="55" r="1.8" fill={BORE} />
          </g>
        )}

        {/* PISTOL */}
        {weaponId === 'pistol' && (
          <g>
            {/* Slide, painted, with the ejection port cut into it. */}
            <path
              d="M 48,30 L 184,30 L 186,42 L 186,54 L 48,56 Z"
              fill={paint}
              stroke={STEEL}
              strokeWidth="1"
            />
            <path d="M 48,30 L 184,30 L 186,42 L 186,54 L 48,56 Z" fill={`url(#${patternId})`} />
            <path d="M 48,30 L 184,30 L 186,42 L 186,54 L 48,56 Z" fill={`url(#${glossId})`} />
            <rect x="118" y="34" width="34" height="11" rx="2" fill={BORE} opacity="0.85" />
            {/* Rear serrations. */}
            {[54, 60, 66, 72].map((x) => (
              <line
                key={x}
                x1={x}
                y1="33"
                x2={x}
                y2="53"
                stroke="rgba(0,0,0,0.55)"
                strokeWidth="2.4"
              />
            ))}
            {/* Sights and bore. */}
            <path d="M 56,30 L 68,30 L 68,25 L 56,25 Z" fill={STEEL} />
            <rect x="55" y="25" width="4" height="5" fill={BORE} />
            <rect x="170" y="24" width="5" height="6" fill={STEEL} />
            <rect x="184" y="36" width="8" height="12" rx="1" fill={STEEL} />
            <circle cx="188" cy="42" r="3" fill={BORE} />
            {/* Frame, grip and magazine baseplate. */}
            <path
              d="M 62,56 L 96,56 L 86,92 L 58,89 Z"
              fill={POLYMER}
              stroke={STEEL}
              strokeWidth="1.2"
            />
            <path d="M 66,62 L 90,62 L 84,84 L 62,82 Z" fill="rgba(255,255,255,0.05)" />
            {[0, 1, 2, 3].map((i) => (
              <line
                key={i}
                x1={64 + i * 1.5}
                y1={64 + i * 5}
                x2={90 - i}
                y2={62 + i * 5}
                stroke="rgba(0,0,0,0.4)"
                strokeWidth="1.4"
              />
            ))}
            <path d="M 57,88 L 87,91 L 86,96 L 56,93 Z" fill={STEEL} />
            {/* Trigger guard and trigger. */}
            <path
              d="M 96,56 Q 116,66 112,78 L 96,78"
              fill="none"
              stroke={POLYMER}
              strokeWidth="4"
            />
            <path d="M 98,60 Q 102,68 97,73" fill="none" stroke={accentColor} strokeWidth="2.4" />
            {/* Accessory rail under the dust cover. */}
            <path d="M 120,56 L 168,56 L 168,62 L 120,62 Z" fill={POLYMER} />
            {[126, 136, 146, 156].map((x) => (
              <line key={x} x1={x} y1="57" x2={x} y2="61" stroke={STEEL} strokeWidth="1.4" />
            ))}
          </g>
        )}

        {/* ASSAULT RIFLE */}
        {weaponId === 'assault' && (
          <g>
            {/* Buffer-tube stock with a cheek weld, then the painted receiver. */}
            <path d="M 8,44 L 20,44 L 20,64 L 8,62 Z" fill={STEEL} />
            <path
              d="M 20,42 L 52,40 L 52,60 L 20,63 Z"
              fill={POLYMER}
              stroke={STEEL}
              strokeWidth="1"
            />
            <path d="M 24,44 L 50,42 L 50,49 L 24,50 Z" fill="rgba(255,255,255,0.06)" />
            <path
              d="M 52,38 L 148,37 L 148,59 L 52,60 Z"
              fill={paint}
              stroke="rgba(255,255,255,0.22)"
              strokeWidth="1"
            />
            <path d="M 52,38 L 148,37 L 148,59 L 52,60 Z" fill={`url(#${patternId})`} />
            <path d="M 52,38 L 148,37 L 148,59 L 52,60 Z" fill={`url(#${glossId})`} />
            {/* Ejection port and forward assist. */}
            <rect x="96" y="41" width="26" height="9" rx="1.5" fill={BORE} opacity="0.8" />
            <circle cx="128" cy="46" r="3" fill={STEEL} />
            {/* Top rail, ribbed, with a rear aperture sight. */}
            <path d="M 52,33 L 178,33 L 178,38 L 52,38 Z" fill={POLYMER} />
            {[58, 66, 74, 82, 90, 98, 106, 114, 122, 130, 138, 146, 154, 162, 170].map((x) => (
              <line key={x} x1={x} y1="34" x2={x} y2="37" stroke={STEEL} strokeWidth="1.2" />
            ))}
            <path d="M 60,33 L 72,33 L 72,26 L 60,26 Z" fill={STEEL} />
            <circle cx="66" cy="29" r="2.2" fill={BORE} />
            {/* Slotted handguard, painted with the receiver. */}
            <path
              d="M 148,40 L 200,40 L 200,56 L 148,57 Z"
              fill={paint}
              stroke="rgba(255,255,255,0.18)"
            />
            <path d="M 148,40 L 200,40 L 200,56 L 148,57 Z" fill={`url(#${patternId})`} />
            <path d="M 148,40 L 200,40 L 200,56 L 148,57 Z" fill={`url(#${glossId})`} />
            {[154, 168, 182].map((x) => (
              <rect
                key={x}
                x={x}
                y="44"
                width="10"
                height="6"
                rx="1.5"
                fill={BORE}
                opacity="0.75"
              />
            ))}
            {/* Gas block, front sight post, barrel, birdcage. */}
            <path d="M 196,34 L 203,34 L 203,46 L 196,46 Z" fill={STEEL} />
            <path d="M 198,34 L 201,34 L 201,27 L 198,27 Z" fill={STEEL} />
            <rect x="200" y="45" width="26" height="6" fill={BORE} />
            <path d="M 226,42 L 236,42 L 236,54 L 226,54 Z" fill={STEEL} />
            {[45, 49].map((y) => (
              <line key={y} x1="227" y1={y} x2="235" y2={y} stroke={BORE} strokeWidth="1.6" />
            ))}
            {/* Curved magazine, in two segments so the curve is drawn. */}
            <path
              d="M 112,59 Q 122,74 116,86 L 100,83 Q 108,71 100,59 Z"
              fill={POLYMER}
              stroke={STEEL}
              strokeWidth="1"
            />
            <path d="M 116,86 L 100,83 L 99,90 L 115,93 Z" fill={STEEL} />
            <path
              d="M 105,63 Q 112,73 108,82"
              fill="none"
              stroke="rgba(255,255,255,0.12)"
              strokeWidth="2"
            />
            {/* Pistol grip and trigger. */}
            <path
              d="M 66,60 L 82,60 L 74,84 L 58,81 Z"
              fill={POLYMER}
              stroke={STEEL}
              strokeWidth="1"
            />
            <path
              d="M 82,60 Q 96,68 92,78 L 80,78"
              fill="none"
              stroke={POLYMER}
              strokeWidth="3.5"
            />
            <path d="M 84,63 Q 87,70 83,74" fill="none" stroke={accentColor} strokeWidth="2.2" />
          </g>
        )}

        {/* SHOTGUN */}
        {weaponId === 'shotgun' && (
          <g>
            {/* Stock: wrist, comb and recoil pad. */}
            <path d="M 6,44 L 14,44 L 14,68 L 6,66 Z" fill={STEEL} />
            <path
              d="M 14,42 L 50,38 L 50,58 L 14,66 Z"
              fill={POLYMER}
              stroke={STEEL}
              strokeWidth="1"
            />
            <path d="M 18,45 L 47,41 L 47,48 L 18,52 Z" fill="rgba(255,255,255,0.05)" />
            {/* Heavy receiver, painted, with an ejection port and a loading gate. */}
            <path
              d="M 50,36 L 132,36 L 132,62 L 50,60 Z"
              fill={paint}
              stroke="rgba(255,255,255,0.22)"
            />
            <path d="M 50,36 L 132,36 L 132,62 L 50,60 Z" fill={`url(#${patternId})`} />
            <path d="M 50,36 L 132,36 L 132,62 L 50,60 Z" fill={`url(#${glossId})`} />
            <rect x="86" y="40" width="30" height="10" rx="2" fill={BORE} opacity="0.8" />
            <rect x="76" y="56" width="34" height="5" rx="2" fill={accentColor} opacity="0.65" />
            {/* Over-and-under barrels with a rib between them. */}
            <rect x="132" y="38" width="98" height="9" fill={BORE} />
            <rect x="132" y="47" width="98" height="4" fill={STEEL} opacity="0.8" />
            <rect x="132" y="51" width="98" height="9" fill={BORE} />
            <rect x="132" y="38" width="98" height="3" fill="rgba(255,255,255,0.14)" />
            {/* Muzzle rings and bead sight. */}
            <rect x="228" y="36" width="7" height="26" rx="2" fill={STEEL} />
            <circle cx="231" cy="42" r="2.6" fill={BORE} />
            <circle cx="231" cy="56" r="2.6" fill={BORE} />
            <circle cx="222" cy="35" r="2.2" fill={accentColor} />
            {/* Ribbed pump, painted, riding under the barrels. */}
            <path d="M 148,49 L 190,49 L 190,64 L 148,64 Z" fill={paint} stroke={STEEL} />
            <path d="M 148,49 L 190,49 L 190,64 L 148,64 Z" fill={`url(#${patternId})`} />
            <path d="M 148,49 L 190,49 L 190,64 L 148,64 Z" fill={`url(#${glossId})`} />
            {[154, 162, 170, 178, 186].map((x) => (
              <line
                key={x}
                x1={x}
                y1="51"
                x2={x}
                y2="62"
                stroke="rgba(0,0,0,0.45)"
                strokeWidth="2.2"
              />
            ))}
            {/* Action bar back to the receiver. */}
            <rect x="128" y="59" width="24" height="3.5" fill={STEEL} />
            {/* Grip and trigger. */}
            <path
              d="M 58,60 L 74,60 L 66,84 L 50,81 Z"
              fill={POLYMER}
              stroke={STEEL}
              strokeWidth="1"
            />
            <path
              d="M 74,60 Q 88,68 84,78 L 72,78"
              fill="none"
              stroke={POLYMER}
              strokeWidth="3.5"
            />
            <path d="M 76,63 Q 79,70 75,74" fill="none" stroke={accentColor} strokeWidth="2.2" />
          </g>
        )}

        {/* SNIPER RIFLE */}
        {weaponId === 'sniper' && (
          <g>
            {/* Skeletonised stock: two rails with a gap, a cheek riser, a pad. */}
            <path d="M 6,44 L 13,44 L 13,66 L 6,64 Z" fill={STEEL} />
            <path d="M 13,42 L 50,40 L 50,47 L 13,49 Z" fill={POLYMER} />
            <path d="M 13,59 L 50,56 L 50,63 L 13,66 Z" fill={POLYMER} />
            <path
              d="M 20,34 L 46,33 L 46,42 L 20,43 Z"
              fill={POLYMER}
              stroke={STEEL}
              strokeWidth="1"
            />
            {/* Chassis, painted. */}
            <path
              d="M 50,40 L 140,38 L 140,58 L 50,60 Z"
              fill={paint}
              stroke="rgba(255,255,255,0.28)"
              strokeWidth="1"
            />
            <path d="M 50,40 L 140,38 L 140,58 L 50,60 Z" fill={`url(#${patternId})`} />
            <path d="M 50,40 L 140,38 L 140,58 L 50,60 Z" fill={`url(#${glossId})`} />
            {/* Bolt: body along the receiver, handle turned down. */}
            <rect x="58" y="42" width="34" height="6" rx="3" fill={STEEL} />
            <path d="M 88,45 L 100,38 L 105,42 L 93,49 Z" fill={STEEL} />
            <circle cx="102" cy="40" r="3.4" fill={accentColor} />
            {/* Heavy barrel stepping down, then a ported brake. */}
            <rect x="140" y="42" width="34" height="10" fill={BORE} />
            <rect x="174" y="44" width="52" height="6" fill={BORE} />
            <rect x="140" y="42" width="34" height="2.5" fill="rgba(255,255,255,0.12)" />
            <path d="M 226,40 L 238,40 L 238,54 L 226,54 Z" fill={STEEL} />
            {[43, 47, 51].map((y) => (
              <line key={y} x1="227" y1={y} x2="237" y2={y} stroke={BORE} strokeWidth="1.6" />
            ))}
            {/* Scope: body, both bells, turrets, mounts. */}
            <rect x="78" y="22" width="52" height="11" rx="2.5" fill={POLYMER} stroke={STEEL} />
            <path d="M 70,19 L 78,22 L 78,33 L 70,36 Z" fill={POLYMER} stroke={STEEL} />
            <path d="M 130,21 L 140,18 L 140,37 L 130,34 Z" fill={POLYMER} stroke={STEEL} />
            <rect x="138" y="18" width="3" height="19" fill={accentColor} opacity="0.7" />
            <rect x="98" y="16" width="10" height="7" rx="1.5" fill={STEEL} />
            <rect x="94" y="26" width="7" height="9" rx="1.5" fill={STEEL} />
            <rect x="84" y="33" width="6" height="7" fill={STEEL} />
            <rect x="118" y="33" width="6" height="7" fill={STEEL} />
            {/* Straight box magazine, the shape that tells it from the rifle. */}
            <rect x="96" y="58" width="19" height="20" rx="1.5" fill={POLYMER} stroke={STEEL} />
            <rect x="95" y="76" width="21" height="4" rx="1" fill={STEEL} />
            {/* Grip, trigger, and a bipod folded down under the handguard. */}
            <path
              d="M 64,60 L 80,60 L 73,82 L 57,79 Z"
              fill={POLYMER}
              stroke={STEEL}
              strokeWidth="1"
            />
            <path
              d="M 80,60 Q 93,68 89,77 L 78,77"
              fill="none"
              stroke={POLYMER}
              strokeWidth="3.5"
            />
            <path d="M 82,63 Q 85,69 81,73" fill="none" stroke={accentColor} strokeWidth="2.2" />
            <line x1="186" y1="50" x2="176" y2="80" stroke={STEEL} strokeWidth="2.6" />
            <line x1="186" y1="50" x2="196" y2="80" stroke={STEEL} strokeWidth="2.6" />
            <line x1="172" y1="80" x2="180" y2="80" stroke={STEEL} strokeWidth="2.6" />
            <line x1="192" y1="80" x2="200" y2="80" stroke={STEEL} strokeWidth="2.6" />
          </g>
        )}

        {/* Wear last, so the scratches sit on top of everything they mark. */}
        <Wear seed={patternSeed} floatValue={floatValue} />
      </svg>
    </div>
  );
}
