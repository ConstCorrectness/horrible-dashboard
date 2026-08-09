/**
 * How a game identifies itself in the UI: its icon and its accent.
 *
 * These two maps were copy-pasted across LobbyPanel, PlaySection, GameBoardPanel and
 * ChallengeCards (four identical icon maps, three identical accent maps). Adding a
 * game meant remembering all of them, and retinting meant editing 33 hexes — so they
 * live here once and every panel imports them.
 *
 * **The accents are identification, not decoration, and not the interactive voltage.**
 * The app runs a single accent (`--accent`) for everything you can click: buttons,
 * focus rings, active states. These colours exist only so a game is recognizable at a
 * glance in a shelf of eleven — so they're used *only* for small marks (a card edge,
 * an icon chip) and are deliberately low-chroma so they never compete with the
 * voltage. A saturated accent-adjacent hue here would out-shout the buttons and break
 * the one-voltage rule. Keep new entries in the same register: low chroma, nothing
 * that reads as "press me".
 *
 * See `.games-theme` in games.css.
 */

/** Icon per catalog game; anything unrecognized gets the die. */
export const GAME_ICONS: Record<string, string> = {
  tictactoe: '❌',
  connect_four: '🔴',
  holdem: '🃏',
  rag_race: '📚',
  code_golf: '⛳',
  test_duel: '⚖️',
  bug_hunt: '🐛',
  arena: '🤖',
  fighter: '🥊',
  vizdoom_toy: '🔫',
  vizdoom_duel: '💀',
};

/** Identification accent per game — small marks only. See the note above. */
export const GAME_ACCENT: Record<string, string> = {
  tictactoe: '#b45c4a',
  connect_four: '#c98a2e',
  holdem: '#8a6ea8',
  rag_race: '#6f86a8',
  code_golf: '#6f9464',
  test_duel: '#8a8578',
  bug_hunt: '#87914e',
  arena: '#c07a45',
  fighter: '#b05a52',
  vizdoom_toy: '#9c4038',
  vizdoom_duel: '#8f5f96',
};

/**
 * A one-line tagline per game — the card subtitle in the library sidebar. Kept short
 * (a phrase, not the paragraph-length blurbs in PlaySection's GAME_DESCRIPTIONS) so it
 * sits on one or two lines under the title inside a card.
 */
export const GAME_TAGLINES: Record<string, string> = {
  tictactoe: 'Classic 3-in-a-row search test',
  connect_four: 'Drop four in a row — deeper trees',
  holdem: "Limit hold'em under imperfect info",
  rag_race: 'Retrieval-augmented Q&A sprint',
  code_golf: 'Shortest Python that passes',
  test_duel: 'Test-cover their code, defend yours',
  bug_hunt: 'Find and patch bugs in a codebase',
  arena: 'Real-time survival grid world',
  fighter: '2D arcade street fighting',
  vizdoom_toy: '3D Doom visual combat sim',
  vizdoom_duel: 'Networked 1v1 Doom deathmatch',
};

/**
 * Decision class per game id — the fallback when only an id is known (the catalog
 * entry carries the authoritative `decision_class` from the backend `GameSpec`). A
 * `policy` game is an `obs → action` mapping (bot-shaped); a `reasoner` game needs
 * the LLM harness. Keep this in sync with each engine's `register_game`.
 */
export const GAME_DECISION_CLASS: Record<string, 'policy' | 'reasoner'> = {
  tictactoe: 'policy',
  connect_four: 'policy',
  holdem: 'policy',
  arena: 'policy',
  fighter: 'policy',
  vizdoom_toy: 'policy',
  vizdoom_duel: 'policy',
  rag_race: 'reasoner',
  code_golf: 'reasoner',
  test_duel: 'reasoner',
  bug_hunt: 'reasoner',
  tabular_fe: 'reasoner',
};

/** The decision class for a game id (fallback map; prefer the catalog entry's own
 * `decision_class` when you have it). Uncatalogued games default to `policy`. */
export function decisionClassOf(id: string): 'policy' | 'reasoner' {
  return GAME_DECISION_CLASS[id] ?? 'policy';
}

/** A short badge for a decision class — the glyph + label shown on game cards and
 * in the builder header so the category split is legible at a glance. */
export function decisionClassBadge(cls: 'policy' | 'reasoner'): { icon: string; label: string } {
  return cls === 'reasoner' ? { icon: '🧠', label: 'Reasoner' } : { icon: '⚙', label: 'Policy' };
}

/**
 * The two **categories** the library is split into, in the order they're shown.
 *
 * The wire values stay `policy`/`reasoner` (the backend `GameSpec` is the source of
 * truth and renaming them would migrate nothing useful); these are what a player
 * reads. "Coded" and "LLM" name *what you build* rather than what the seat is
 * formally called, because the choice a player is making when they pick a shelf is
 * "am I writing a policy or engineering a prompt".
 *
 * Coded comes first: it needs no model configured, so it's the half of the library
 * that works on a fresh install.
 */
export const GAME_CATEGORIES: {
  cls: 'policy' | 'reasoner';
  icon: string;
  label: string;
  blurb: string;
}[] = [
  {
    cls: 'policy',
    icon: '⚙',
    label: 'Coded agents',
    blurb: 'You write the policy: obs → action, no model in the loop.',
  },
  {
    cls: 'reasoner',
    icon: '🧠',
    label: 'LLM agents',
    blurb: 'You engineer the harness: system prompt, tools, model.',
  },
];

/** The icon for a game id, falling back to the die for anything uncatalogued. */
export function gameIcon(id: string): string {
  return GAME_ICONS[id] ?? '🎲';
}

/** The card tagline for a game id, with a neutral fallback for uncatalogued games. */
export function gameTagline(id: string): string {
  return GAME_TAGLINES[id] ?? 'Agent competition environment';
}

/** The identification accent for a game id, falling back to the module voltage. */
export function gameAccent(id: string): string {
  return GAME_ACCENT[id] ?? 'var(--accent)';
}

/**
 * Hero artwork per game — the full-bleed background behind the Quick Play hero
 * (`GamesHero`). **Empty on purpose: there is no game art in the repo yet.**
 *
 * With no entry, the hero composes a background procedurally from the game's accent
 * and glyph (an accent wash + oversized mark + scrim, the same recipe as the library
 * cards), so the slideshow works today and every new game gets a usable hero for
 * free. Dropping a real image in later is a one-line entry here — no component
 * change, and games with art can coexist with games without it.
 *
 * When art does land: **self-host it** (`packages/ui/src/assets/games/`, imported so
 * Vite fingerprints it), never a CDN URL. The app is offline-first and ships as a
 * Tauri desktop build — the same rule the webfonts follow. Landscape, and dark or
 * dimmable: white display type and the scrim sit on top of it.
 */
export const GAME_HERO: Record<string, string> = {};

/** The hero image URL for a game id, or null to compose one from its identity. */
export function gameHeroImage(id: string): string | null {
  return GAME_HERO[id] ?? null;
}
