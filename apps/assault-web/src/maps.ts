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
  /** The modes a room here may open in, when it is not what `sites` implies. */
  modes?: readonly string[];
  /**
   * A training map: its card opens the offline aim range (`TRAIN_MODE`), with
   * the drills' moving targets, rather than a room nobody else is in.
   */
  range?: boolean;
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
  {
    id: 'hd_aim',
    title: 'Aim Range',
    blurb: 'Lanes and a strafe gallery: moving-target drills, or aim and AWP duels.',
    sites: false,
    modes: ['dm', 'aim', 'awp'],
    range: true,
  },
];

/** The modes Create match offers, in order. Duel is not one: it is a queue. */
export const MODE_OPTIONS = ['dm', 'tdm', 'defuse', 'retake', 'aim', 'awp'] as const;

/**
 * The offline aim range. Not a server mode — nothing is opened on the game
 * server, the targets are the client's own (`training.ts`) — so it is never in
 * `modesFor`, only in what a range map's menu offers (`menuModes`).
 */
export const TRAIN_MODE = 'train';

/** What Create match lists for `id`: the range first on a range map, then its room modes. */
export function menuModes(id: string): string[] {
  return mapEntry(id).range ? [TRAIN_MODE, ...MODE_OPTIONS] : [...MODE_OPTIONS];
}

/** What a map's card opens: the range on a range map, else deathmatch. */
export function defaultMode(id: string): string {
  return mapEntry(id).range ? TRAIN_MODE : 'dm';
}

/**
 * The modes a room on `id` may open in. The server's list (`/hassault/maps`,
 * from each map's own source) wins when it has arrived; before then, or if it
 * cannot be fetched, what this page knows about the map, so the menu never
 * offers a room the server would refuse.
 */
export function modesFor(id: string, served?: Record<string, readonly string[]> | null): string[] {
  const listed = served?.[id];
  if (listed) return MODE_OPTIONS.filter((m) => listed.includes(m));
  const entry = mapEntry(id);
  if (entry.modes) return [...entry.modes];
  return entry.sites ? ['dm', 'tdm', 'defuse', 'retake'] : ['dm', 'tdm'];
}

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
    case 'retake':
      return 'Retake';
    case TRAIN_MODE:
      return 'Aim Training';
    case 'aim':
      return 'Aim Arena';
    case 'awp':
      return 'AWP Arena';
    case 'duel':
      return 'Duel';
    default:
      return mode.toUpperCase();
  }
}
