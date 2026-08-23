/**
 * Character Faction and Operator Skin definitions for hAssault.
 *
 * Defines color palettes, material specs, and visual trim for character models.
 * Strictly aesthetic: does NOT change any hitbox dimension or collision volume.
 */

export interface AvatarSkinPalette {
  id: string;
  name: string;
  faction: 'arc' | 'halon' | 'neutral';
  bodyColor: number;
  armorColor: number;
  trimColor: number;
  visorColor: number;
  skinToneColor: number;
  bootColor: number;
  roughness: number;
  metalness: number;
}

export const AVATAR_SKIN_CATALOG: Record<string, AvatarSkinPalette> = {
  // ARC Default: Scavenger Operator
  arc_default: {
    id: 'arc_default',
    name: 'ARC Scavenger',
    faction: 'arc',
    bodyColor: 0xd9a441, // Amber / Desert Sand
    armorColor: 0x4a3b2c, // Weathered Brown
    trimColor: 0xf2e2c4, // Sand Trim
    visorColor: 0x222222, // Dark Ballistic Visor
    skinToneColor: 0xc89d7c,
    bootColor: 0x2b231d,
    roughness: 0.8,
    metalness: 0.15,
  },

  // HALON Default: Custodial Enforcer
  halon_default: {
    id: 'halon_default',
    name: 'HALON Enforcer',
    faction: 'halon',
    bodyColor: 0x4c8fd4, // Steel Blue
    armorColor: 0x1e293b, // Tactical Slate Dark
    trimColor: 0x94a3b8, // Light Steel
    visorColor: 0x38bdf8, // Cyan Visor Glow
    skinToneColor: 0xdfb190,
    bootColor: 0x0f172a,
    roughness: 0.6,
    metalness: 0.35,
  },

  // Custom Agent Skins
  scavenger_prime: {
    id: 'scavenger_prime',
    name: 'Scavenger Prime',
    faction: 'arc',
    bodyColor: 0xb45309, // Deep Rust Amber
    armorColor: 0x1c1917, // Matte Carbon
    trimColor: 0xf59e0b, // Neon Amber Stripe
    visorColor: 0xfbbf24, // Amber Optical Lens
    skinToneColor: 0xa16207,
    bootColor: 0x0c0a09,
    roughness: 0.75,
    metalness: 0.4,
  },

  cyber_ghost: {
    id: 'cyber_ghost',
    name: 'Ghost Recon',
    faction: 'neutral',
    bodyColor: 0x18181b, // Stealth Black
    armorColor: 0x27272a, // Charcoal Plate
    trimColor: 0xa855f7, // Cyber Purple Trim
    visorColor: 0xc084fc, // Purple Hologram Visor
    skinToneColor: 0xd4d4d8,
    bootColor: 0x09090b,
    roughness: 0.5,
    metalness: 0.6,
  },

  arctic_specops: {
    id: 'arctic_specops',
    name: 'Arctic SpecOps',
    faction: 'neutral',
    bodyColor: 0xe2e8f0, // Snow White Camo
    armorColor: 0x64748b, // Frost Slate
    trimColor: 0x38bdf8, // Ice Cyan
    visorColor: 0x0ea5e9, // Polar Mirror Lens
    skinToneColor: 0xf1f5f9,
    bootColor: 0x334155,
    roughness: 0.65,
    metalness: 0.25,
  },

  hazmat_warden: {
    id: 'hazmat_warden',
    name: 'Hazmat Warden',
    faction: 'halon',
    bodyColor: 0xeab308, // Hazard Yellow
    armorColor: 0x18181b, // Heavy Rubber / Carbon
    trimColor: 0xef4444, // Warning Red Trim
    visorColor: 0x22c55e, // Biohazard Green Lens
    skinToneColor: 0xca8a04,
    bootColor: 0x1c1917,
    roughness: 0.85,
    metalness: 0.1,
  },
};

/**
 * Resolve skin palette for a player row given team index (0: ARC, 1: HALON)
 * and optional custom skin ID.
 */
export function resolveAvatarSkin(team: number, skinId?: string): AvatarSkinPalette {
  if (skinId && AVATAR_SKIN_CATALOG[skinId]) {
    return AVATAR_SKIN_CATALOG[skinId];
  }
  return team === 1 ? AVATAR_SKIN_CATALOG.halon_default : AVATAR_SKIN_CATALOG.arc_default;
}
