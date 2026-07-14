import { revealRegionView } from '../../layout/controller';
import { registry, type ModuleManifest } from '../../registry';
import { setSetting } from '../../settings';
import './games.css';
import { openGamesHub } from './hub-section';
import { AgentThoughtsPane } from './panels/AgentThoughtsPane';
import { ChallengesPanel } from './panels/ChallengesPanel';
import { FighterArcadePanel } from './panels/FighterArcadePanel';
import { GameBoardPanel } from './panels/GameBoardPanel';
import { LeaderboardPanel } from './panels/LeaderboardPanel';
import { LoadoutPanel } from './panels/LoadoutPanel';
import { LobbyPanel } from './panels/LobbyPanel';
import { PlazaPanel } from './panels/PlazaPanel';
import { ProfilePanel } from './panels/ProfilePanel';
import { ReplayBrowserPanel } from './panels/ReplayBrowserPanel';
import { ReplayViewerPanel } from './panels/ReplayViewerPanel';
import { RosterPanel } from './panels/RosterPanel';
import { TownPanel } from './panels/TownPanel';

/**
 * Games module: watch your agent play turn-based games against another user's
 * agent, refereed by the central game server. The **Games hub** (`games.lobby`)
 * is the Play/matchmaking entry point; Ladder, Challenges, Replays, Players, and
 * Profile are standalone tool panels on the left activity rail (they used to be
 * hub tabs). The board renders the live game. See docs/modules/games.mdx.
 */
export const gamesModule: ModuleManifest = {
  id: 'games',
  title: 'Games',
  panels: [
    {
      id: 'games.lobby',
      title: 'Games',
      component: LobbyPanel,
      role: 'document',
      icon: '🕹',
      singleton: true,
    },
    {
      id: 'games.board',
      title: 'Game Board',
      component: GameBoardPanel,
      role: 'document',
      icon: '▦',
      singleton: true,
    },
    {
      id: 'games.loadout',
      title: 'Agent Harness',
      component: LoadoutPanel,
      role: 'document',
      icon: '🛠',
      singleton: true,
      editor: true,
    },
    // The former hub tabs, now standalone tool panels on the activity rail.
    {
      id: 'games.ladder',
      title: 'Ladder',
      component: LeaderboardPanel,
      role: 'tool',
      icon: '🏆',
      defaultDock: 'left',
      singleton: true,
    },
    {
      id: 'games.challenges',
      title: 'Challenges',
      component: ChallengesPanel,
      role: 'tool',
      icon: '🎯',
      defaultDock: 'left',
      singleton: true,
    },
    {
      id: 'games.replays',
      title: 'Replays',
      component: ReplayBrowserPanel,
      role: 'tool',
      icon: '📼',
      defaultDock: 'left',
      singleton: true,
    },
    {
      id: 'games.players',
      title: 'Players',
      component: RosterPanel,
      role: 'tool',
      icon: '👥',
      defaultDock: 'left',
      singleton: true,
    },
    {
      id: 'games.profile',
      title: 'Profile',
      component: ProfilePanel,
      role: 'tool',
      icon: '🪪',
      defaultDock: 'left',
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
      id: 'games.thoughts',
      title: 'Agent Thoughts',
      component: AgentThoughtsPane,
      role: 'document',
      icon: '💭',
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
    {
      id: 'games.plaza',
      title: 'The Plaza',
      component: PlazaPanel,
      role: 'document',
      icon: '🏛',
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
      run: () => revealRegionView('games.board'),
    },
    {
      id: 'games.openLoadout',
      title: 'Games: Edit agent harness',
      // Opens the harness as its own standalone pane (a companion renders its bare
      // component when opened directly) rather than docking it in the arcade shell —
      // the harness is a first-class authoring surface (see the Coding Harnesses
      // workspace). The in-arcade `t` toggle and the Lobby/Town "edit harness" buttons
      // still reveal it inside their shell.
      run: () => registry.openPanel('games.loadout'),
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
      run: () => registry.openPanel('games.profile'),
    },
    {
      id: 'games.openArcade',
      title: 'Games: Open arcade fighter',
      run: () => registry.openPanel('games.arcade'),
    },
    {
      id: 'games.openThoughts',
      title: 'Games: Open agent thoughts',
      run: () => revealRegionView('games.thoughts'),
    },
    {
      id: 'games.openReplays',
      title: 'Games: Browse replays',
      run: () => registry.openPanel('games.replays'),
    },
    {
      id: 'games.openLeaderboard',
      title: 'Games: Open ladder',
      run: () => registry.openPanel('games.ladder'),
    },
    {
      id: 'games.openChallenges',
      title: 'Games: Open challenge track',
      run: () => registry.openPanel('games.challenges'),
    },
    {
      id: 'games.openTown',
      title: 'Games: Visit AgentTown',
      run: () => registry.openPanel('games.town'),
    },
    {
      id: 'games.openPlaza',
      title: 'Games: Enter the Plaza',
      run: () => registry.openPanel('games.plaza'),
    },
    {
      id: 'games.openRoster',
      title: 'Games: Open players',
      run: () => registry.openPanel('games.players'),
    },
  ],
  settings: [
    {
      key: 'games.serverUrl',
      title: 'Game server URL',
      description:
        'WebSocket URL of the central game server. Defaults to the hosted server; set ws://localhost:9200 to use a local one.',
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
    {
      key: 'games.google.clientSecret',
      title: 'Google OAuth client secret',
      description:
        "Google's device flow requires the client secret at the token poll (unlike GitHub). On a hosted server prefer the GAMES_GOOGLE_CLIENT_SECRET env secret.",
      type: 'string',
      default: '',
    },
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
