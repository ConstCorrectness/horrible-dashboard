import type { CSSProperties } from 'react';

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
  const gradId = `wep-grad-${weaponId}-${baseColor.replace('#', '')}-${accentColor.replace('#', '')}-${patternSeed}`;
  const wearOpacity = Math.max(0.35, 1.0 - floatValue * 0.65);

  const inspectTransform = isInspecting
    ? 'rotate(-12deg) scale(1.08) translateY(-6px)'
    : 'rotate(0deg) scale(1) translateY(0px)';

  return (
    <div
      className={className}
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        transition: 'transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)',
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
          <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor={baseColor} stopOpacity={wearOpacity} />
            <stop offset="50%" stopColor={accentColor} stopOpacity={wearOpacity} />
            <stop offset="100%" stopColor={baseColor} stopOpacity={wearOpacity * 0.9} />
          </linearGradient>

          <pattern id={`pat-${gradId}`} width="20" height="20" patternUnits="userSpaceOnUse">
            {patternType === 'patina' || patternType === 'fade' ? (
              <circle cx="10" cy="10" r="8" fill={accentColor} opacity="0.3" />
            ) : patternType === 'camo' ? (
              <path d="M0,0 L10,10 L0,20 Z" fill={accentColor} opacity="0.25" />
            ) : (
              <line x1="0" y1="0" x2="20" y2="20" stroke={accentColor} strokeWidth="1.5" opacity="0.4" />
            )}
          </pattern>
        </defs>

        {/* KNIFE SILHOUETTE */}
        {weaponId === 'knife' && (
          <g>
            {/* Blade */}
            <path
              d="M 60,50 Q 140,25 210,35 Q 180,65 110,60 L 60,60 Z"
              fill={`url(#${gradId})`}
              stroke="rgba(255,255,255,0.4)"
              strokeWidth="1.5"
            />
            {/* Grip & Pommel */}
            <path d="M 20,48 L 60,48 L 58,62 L 18,62 Z" fill="#18181b" stroke="#3f3f46" strokeWidth="1" />
            <circle cx="24" cy="55" r="3" fill="#52525b" />
            <circle cx="40" cy="55" r="3" fill="#52525b" />
            {/* Bevel reflection */}
            <path d="M 75,52 Q 135,35 195,40" stroke="rgba(255,255,255,0.7)" strokeWidth="1.2" fill="none" />
          </g>
        )}

        {/* PISTOL SILHOUETTE */}
        {weaponId === 'pistol' && (
          <g>
            {/* Slide */}
            <rect x="50" y="32" width="130" height="24" rx="2" fill={`url(#${gradId})`} stroke="#3f3f46" strokeWidth="1" />
            {/* Barrel tip */}
            <rect x="180" y="38" width="10" height="12" fill="#09090b" />
            {/* Slide Serrations */}
            <line x1="60" y1="36" x2="60" y2="52" stroke="rgba(0,0,0,0.5)" strokeWidth="2" />
            <line x1="66" y1="36" x2="66" y2="52" stroke="rgba(0,0,0,0.5)" strokeWidth="2" />
            <line x1="72" y1="36" x2="72" y2="52" stroke="rgba(0,0,0,0.5)" strokeWidth="2" />
            {/* Grip Frame */}
            <path d="M 70,56 L 90,56 L 80,92 L 58,90 Z" fill="#18181b" stroke="#27272a" strokeWidth="1.2" />
            {/* Trigger Guard & Trigger */}
            <path d="M 90,56 Q 110,68 108,76 L 95,76" fill="none" stroke="#27272a" strokeWidth="2" />
            <path d="M 96,62 Q 98,70 94,72" fill="none" stroke={accentColor} strokeWidth="2" />
          </g>
        )}

        {/* ASSAULT RIFLE SILHOUETTE */}
        {weaponId === 'assault' && (
          <g>
            {/* Stock */}
            <path d="M 15,50 L 55,42 L 55,62 L 20,68 Z" fill="#18181b" stroke="#27272a" />
            {/* Receiver / Body */}
            <path d="M 55,42 L 155,40 L 155,58 L 55,60 Z" fill={`url(#${gradId})`} stroke="rgba(255,255,255,0.2)" strokeWidth="1" />
            {/* Handguard */}
            <rect x="155" y="44" width="45" height="12" fill={`url(#${gradId})`} />
            {/* Barrel & Muzzle */}
            <rect x="200" y="47" width="30" height="6" fill="#09090b" />
            <rect x="230" y="45" width="6" height="10" fill="#27272a" />
            {/* Curved Magazine */}
            <path d="M 115,58 Q 130,82 110,95 L 98,92 Q 115,75 105,58 Z" fill="#18181b" stroke="#3f3f46" strokeWidth="1" />
            {/* Pistol Grip */}
            <path d="M 68,60 L 80,60 L 72,82 L 60,80 Z" fill="#18181b" />
            {/* Sight */}
            <polygon points="148,40 152,32 156,40" fill="#27272a" />
            <polygon points="215,47 218,36 221,47" fill="#27272a" />
          </g>
        )}

        {/* SHOTGUN SILHOUETTE */}
        {weaponId === 'shotgun' && (
          <g>
            {/* Stock */}
            <path d="M 10,48 L 50,42 L 50,60 L 15,68 Z" fill="#18181b" />
            {/* Heavy Receiver */}
            <rect x="50" y="40" width="85" height="22" rx="2" fill={`url(#${gradId})`} stroke="rgba(255,255,255,0.2)" />
            {/* Twin Barrel */}
            <rect x="135" y="43" width="95" height="9" fill="#09090b" />
            <rect x="135" y="52" width="80" height="6" fill="#18181b" />
            {/* Pump Handle */}
            <rect x="145" y="49" width="35" height="12" rx="2" fill={`url(#${gradId})`} stroke="#27272a" />
            {/* Grip */}
            <path d="M 60,62 L 72,62 L 65,82 L 54,80 Z" fill="#18181b" />
          </g>
        )}

        {/* SNIPER RIFLE SILHOUETTE */}
        {weaponId === 'sniper' && (
          <g>
            {/* Stock & Cheekrest */}
            <path d="M 10,50 L 55,44 L 55,62 L 15,70 Z" fill="#18181b" stroke="#27272a" />
            {/* Main Chassis */}
            <path d="M 55,44 L 140,42 L 140,58 L 55,60 Z" fill={`url(#${gradId})`} stroke="rgba(255,255,255,0.3)" strokeWidth="1" />
            {/* Long Heavy Barrel */}
            <rect x="140" y="46" width="90" height="6" fill="#09090b" />
            {/* Muzzle Brake */}
            <rect x="230" y="43" width="8" height="12" fill="#27272a" />
            {/* High-Powered Scope */}
            <rect x="80" y="28" width="55" height="10" rx="2" fill="#18181b" stroke="#3f3f46" />
            <polygon points="75,26 80,28 80,38 75,40" fill="#27272a" />
            <polygon points="135,28 142,26 142,40 135,38" fill="#27272a" />
            <line x1="92" y1="38" x2="92" y2="42" stroke="#3f3f46" strokeWidth="2" />
            <line x1="122" y1="38" x2="122" y2="42" stroke="#3f3f46" strokeWidth="2" />
            {/* Bipod Legs */}
            <line x1="190" y1="52" x2="182" y2="78" stroke="#3f3f46" strokeWidth="2" />
            <line x1="190" y1="52" x2="198" y2="78" stroke="#3f3f46" strokeWidth="2" />
            {/* Grip & Mag */}
            <path d="M 68,60 L 80,60 L 74,80 L 62,78 Z" fill="#18181b" />
            <rect x="98" y="58" width="16" height="18" fill="#18181b" stroke="#3f3f46" />
          </g>
        )}
      </svg>
    </div>
  );
}
