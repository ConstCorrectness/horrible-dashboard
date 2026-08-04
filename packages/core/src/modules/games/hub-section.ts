/**
 * Which section the single **Games pane** is showing.
 *
 * The Games pane (`games.lobby`, `GamesPanel`) is one pane with six sections —
 * Play, Board, Build, Replays, Career, Social. They used to be separately-openable
 * panes tiled side by side; they're merged now, so "open the board" means "switch
 * this pane's section", not "open another pane".
 *
 * This used to be a module-level store. It is now a thin adapter over the frame
 * engine's `sections` primitive (`PaneState.activeSection`), which is strictly
 * better in three ways the singleton could not be: the choice **persists** with
 * the layout instead of resetting on reload, two panes of the view would be two
 * independent places rather than one shared value, and the host renders the tab
 * strip — so a keybinding, the command palette and the agent's `show("build")`
 * all drive the same state the buttons do.
 *
 * The function shapes are unchanged on purpose: every hand-off site
 * (`revealBoard`, the builder's "back to play", the first-run hero) kept working
 * untouched. See docs/modules/games.mdx and docs/architecture/windowing.mdx.
 */
import { revealSection, sectionsOf, setPaneSection } from '../../layout/controller';
import { findPaneAnywhere, listPanes } from '../../layout/model';
import { layoutStore } from '../../layout/store';
import { usePaneSection } from '../../layout/use-sections';

export type GamesSection = 'play' | 'board' | 'build' | 'replays' | 'career' | 'social';

export const GAMES_VIEW_ID = 'games.lobby';

export const SECTION_LABEL: Record<GamesSection, string> = {
  play: 'Play',
  board: 'Game Board',
  build: 'Build',
  replays: 'Replays',
  career: 'Career',
  social: 'Social',
};

export const SECTION_ICON: Record<GamesSection, string> = {
  play: '🕹',
  board: '▦',
  build: '🛠',
  replays: '📼',
  career: '🪪',
  social: '🏛',
};

const SECTION_IDS = ['play', 'board', 'build', 'replays', 'career', 'social'] as const;

/** Section declarations for the manifest — one source of truth for id/label/icon. */
export const GAMES_SECTIONS = SECTION_IDS.map((id) => ({
  id,
  label: SECTION_LABEL[id],
  icon: SECTION_ICON[id],
  default: id === 'play',
}));

/** The open Games pane instance, if there is one. */
function gamesInstanceId(): string | null {
  const frame = layoutStore.getSnapshot().frame;
  return listPanes(frame).find((p) => p.pane.viewId === GAMES_VIEW_ID)?.pane.instanceId ?? null;
}

function isGamesSection(value: unknown): value is GamesSection {
  return typeof value === 'string' && sectionsOf(GAMES_VIEW_ID).some((s) => s.id === value);
}

export function getGamesSection(): GamesSection {
  const id = gamesInstanceId();
  const pane = id ? findPaneAnywhere(layoutStore.getSnapshot().frame, id)?.pane : null;
  const active = pane?.activeSection;
  return isGamesSection(active) ? active : 'play';
}

/** Switch the Games pane's section, if it is open. Use `openGamesSection` to also open it. */
export function setGamesSection(next: GamesSection): void {
  const id = gamesInstanceId();
  if (id) setPaneSection(id, next);
}

/**
 * The section of the pane this component is rendering in.
 *
 * Reads the *calling pane's* instance rather than a global, so it is correct
 * inside the Games pane and inert outside it — the singleton it replaces
 * answered for whichever pane wrote to it last.
 */
export function useGamesSection(): GamesSection {
  const { section } = usePaneSection();
  return isGamesSection(section) ? section : 'play';
}

/** Open (or focus) the Games pane on `section` — the one entry point every
 * "show me the board / the builder" hand-off goes through. */
export function openGamesSection(next: GamesSection): void {
  revealSection(next, GAMES_VIEW_ID);
}

/** Open (or focus) the Games pane on its Play section. */
export function openGamesHub(): void {
  openGamesSection('play');
}
