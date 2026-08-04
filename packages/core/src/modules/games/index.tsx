import { registry, type ModuleManifest } from '../../registry';
import { setSetting } from '../../settings';
import './games.css';
import { GAMES_SECTIONS, openGamesHub, openGamesSection } from './hub-section';
import { EpisodePanel } from './panels/EpisodePanel';
import { FighterArcadePanel } from './panels/FighterArcadePanel';
import { GamesLogPanel } from './panels/GamesLogPanel';
import { GamesPanel } from './panels/GamesPanel';
import { ReplayViewerPanel } from './panels/ReplayViewerPanel';
import { TownPanel } from './panels/TownPanel';

/**
 * Games module: watch your agent play turn-based games against another user's
 * agent, refereed by the central game server.
 *
 * The **Games pane** (`games.lobby`) is the whole client in one pane: Play, Board,
 * Build, Replays, Career and Social are frame-engine **sections** (declared here,
 * driven through hub-section.ts), and the two spectator surfaces you watch *while*
 * it plays — **Games Log** (`games.log`) and **Episodes** (`games.episodes`) —
 * are its **bottom region strip**.
 *
 * There are no longer separate Ladder / Challenges / Replays / Players / Profile /
 * Plaza panes: every one of them rendered a component this pane already renders in
 * a section, so they were a second home for the same content. They stay reachable
 * by their old names through `VIEW_ALIASES` — see docs/modules/games.mdx.
 */
export const gamesModule: ModuleManifest = {
  id: 'games',
  title: 'Games',
  // DashArena: the game-tuned workspace. The Games client fills the whole window —
  // it is a single-window console (Play / Board / Build / Replays / Career / Social),
  // with the live spectator surfaces (Games Log, Episodes) folded into its own bottom
  // drawer rather than tiled as sibling documents. The standalone Log/Episodes panels
  // stay registered so a power user can still pop one out onto the rail.
  frames: [
    {
      id: 'dasharena',
      name: 'DashArena',
      icon: '🏟',
      frame: {
        center: { pane: 'games.lobby' },
      },
    },
  ],
  panels: [
    {
      id: 'games.lobby',
      title: 'Games',
      component: GamesPanel,
      role: 'document',
      icon: '🕹',
      singleton: true,
      // The console's menu. `GamesPanel` renders the bodies itself (each section
      // holds live state — canvases, unsaved builder code — so they are hidden
      // rather than unmounted); the host renders the strip and persists the choice.
      sections: GAMES_SECTIONS,
      // The spectator surfaces, as a real region strip: resizable, collapsible and
      // persisted per instance, which the hand-rolled drawer was none of.
      regions: [
        { id: 'games.log', label: 'Games Log', icon: '📜', position: 'bottom', defaultSize: 260 },
        { id: 'games.episodes', label: 'Episodes', icon: '🎞', position: 'bottom' },
      ],
    },
    {
      id: 'games.log',
      title: 'Games Log',
      component: GamesLogPanel,
      role: 'document',
      icon: '📜',
      singleton: true,
    },
    {
      id: 'games.episodes',
      title: 'Episodes',
      component: EpisodePanel,
      role: 'document',
      icon: '🎞',
      singleton: true,
    },
    {
      id: 'games.arcade',
      title: 'Arcade Fighter',
      component: FighterArcadePanel,
      role: 'document',
      icon: '🕹',
      singleton: true,
    },
    {
      id: 'games.replay',
      title: 'Replay',
      component: ReplayViewerPanel,
      role: 'document',
      icon: '📼',
      singleton: true,
    },
    {
      id: 'games.town',
      title: 'AgentTown',
      component: TownPanel,
      role: 'document',
      icon: '🏘',
      singleton: true,
    },
  ],
  commands: [
    {
      id: 'games.openLobby',
      title: 'Games: Open lobby',
      run: () => registry.openPanel('games.lobby'),
    },
    {
      id: 'games.openBoard',
      title: 'Games: Open board',
      run: () => openGamesSection('board'),
    },
    {
      id: 'games.openLoadout',
      title: 'Games: Build your agent',
      run: () => openGamesSection('build'),
    },
    {
      id: 'games.restartOnboarding',
      title: 'Games: Restart onboarding',
      run: () => {
        void setSetting('games.onboarded', false);
        openGamesHub();
      },
    },
    {
      id: 'games.openProfile',
      title: 'Games: Open player profile',
      run: () => openGamesSection('career'),
    },
    {
      id: 'games.openArcade',
      title: 'Games: Open arcade fighter',
      run: () => registry.openPanel('games.arcade'),
    },
    {
      id: 'games.openLog',
      title: 'Games: Open games log',
      run: () => registry.openPanel('games.log'),
    },
    {
      id: 'games.openEpisodes',
      title: 'Games: Open episode visualizer',
      run: () => registry.openPanel('games.episodes'),
    },
    {
      id: 'games.openReplays',
      title: 'Games: Browse replays',
      run: () => openGamesSection('replays'),
    },
    {
      id: 'games.openLeaderboard',
      title: 'Games: Open ladder',
      run: () => openGamesSection('career'),
    },
    {
      id: 'games.openChallenges',
      title: 'Games: Open challenge track',
      run: () => openGamesSection('career'),
    },
    {
      id: 'games.openTown',
      title: 'Games: Visit AgentTown',
      run: () => registry.openPanel('games.town'),
    },
    {
      id: 'games.openPlaza',
      title: 'Games: Enter the Plaza',
      run: () => openGamesSection('social'),
    },
    {
      id: 'games.openRoster',
      title: 'Games: Open players',
      run: () => openGamesSection('social'),
    },
  ],
  settings: [
    {
      key: 'games.serverUrl',
      title: 'Game server URL',
      description:
        'WebSocket URL of the central game server. Defaults to the hosted server; set ws://localhost:9090 to use the one `pnpm dev` starts. Ignored when the GAMES_SERVER_URL environment variable is set.',
      type: 'string',
      default: 'wss://horrible-games.fly.dev',
    },
    {
      key: 'games.devToken',
      title: 'Game server dev token',
      description: 'Fallback identity when not signed in (the token is your account id).',
      type: 'string',
      default: 'player',
    },
    {
      key: 'games.github.clientId',
      title: 'GitHub OAuth client id',
      description:
        'Set on the game-server host to enable "Sign in with GitHub" (device flow; no secret needed).',
      type: 'string',
      default: '',
    },
    {
      key: 'games.google.clientId',
      title: 'Google OAuth client id',
      description:
        'Set on the game-server host to enable "Sign in with Google" (device flow; client type "TVs and Limited Input devices").',
      type: 'string',
      default: '',
    },
    // No client-secret setting here on purpose: /api/settings serves the whole bag to
    // the browser, so the game server reads GAMES_GITHUB_CLIENT_SECRET /
    // GAMES_GOOGLE_CLIENT_SECRET from its environment only.
    {
      key: 'games.onboarded',
      title: 'Onboarding complete',
      description:
        "Set when the hub's first-run card finishes (or is dismissed); clear it to see the card again.",
      type: 'boolean',
      default: false,
    },
    {
      key: 'games.sound',
      title: 'Arcade sounds',
      description: 'Small blips on match start, moves, and results.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'games.policy',
      title: 'Move policy',
      description:
        'How this node picks moves: random, agent (local model), bot (custom Python script), or manual (agent tool / UI).',
      type: 'enum',
      default: 'random',
      enumValues: ['random', 'agent', 'manual', 'bot'],
    },
  ],
};
