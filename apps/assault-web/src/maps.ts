/**
 * The maps a browser player can open a room on, and their key art.
 *
 * Titles match each map's own JSON (`backend/modules/hassault/maps/<id>.json`),
 * so the room a player creates is called the same thing on the scoreboard as on
 * this page.
 *
 * The art is a still captured from the game's own renderer — real textures, real
 * light — saved as `src/assets/maps/<id>.webp`. A map with no capture yet simply
 * has no art: the page falls back to its graphite ground, never to a stock image.
 */

export interface MapEntry {
  id: string;
  title: string;
  /** One line: what the place is, for the map card. */
  blurb: string;
  /** Bomb sites present — the room's mode decides whether they're used. */
  sites: boolean;
}

export const MAPS: MapEntry[] = [
  {
    id: 'hd_dust2',
    title: 'Dust II',
    blurb: 'Desert citadel, long sightlines, two sites.',
    sites: true,
  },
  {
    id: 'hd_assault',
    title: 'Assault',
    blurb: 'Warehouse siege across an industrial yard.',
    sites: true,
  },
  {
    id: 'hd_mirage',
    title: 'Mirage',
    blurb: 'Sun-baked courtyard and a contested mid.',
    sites: true,
  },
  {
    id: 'hd_inferno',
    title: 'Inferno',
    blurb: 'Tight Tuscan streets and a banana choke.',
    sites: true,
  },
  { id: 'hd_nuke', title: 'Nuke', blurb: 'Nuclear facility, stacked sites, vents.', sites: true },
  { id: 'hd_office', title: 'Office', blurb: 'Corporate high-rise, close quarters.', sites: true },
  {
    id: 'hd_bank',
    title: 'The Bank',
    blurb: 'Vault heist through lobby and offices.',
    sites: true,
  },
  {
    id: 'hd_facility',
    title: 'Facility',
    blurb: 'Research complex under emergency light.',
    sites: false,
  },
  {
    id: 'hd_junkflea',
    title: 'Junk Flea',
    blurb: 'Scrapyard market, cover everywhere.',
    sites: false,
  },
];

const BY_ID = new Map(MAPS.map((m) => [m.id, m]));

export function mapEntry(id: string): MapEntry {
  return (
    BY_ID.get(id) ?? {
      id,
      title: id.replace(/^hd_/, '').replace(/_/g, ' '),
      blurb: '',
      sites: false,
    }
  );
}

// URLs only — the images themselves load when something renders one.
const ART = import.meta.glob('./assets/maps/*.webp', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>;

export function mapArt(id: string): string | undefined {
  return ART[`./assets/maps/${id}.webp`];
}

/** Room mode ids → what a player calls them. */
export function modeLabel(mode: string): string {
  switch (mode) {
    case 'dm':
      return 'Deathmatch';
    case 'tdm':
      return 'Team DM';
    case 'defuse':
      return 'Defuse';
    case 'ctf':
      return 'Capture';
    default:
      return mode.toUpperCase();
  }
}
