import { revealRegionView } from '../../layout/controller';
import { registry, type ModuleManifest } from '../../registry';
import './games.css';
import { ChallengesPanel } from './panels/ChallengesPanel';
import { GameBoardPanel } from './panels/GameBoardPanel';
import { LeaderboardPanel } from './panels/LeaderboardPanel';
import { LoadoutPanel } from './panels/LoadoutPanel';
import { LobbyPanel } from './panels/LobbyPanel';
import { PlazaPanel } from './panels/PlazaPanel';
import { RosterPanel } from './panels/RosterPanel';
import { TownPanel } from './panels/TownPanel';

/**
 * Games module: watch your agent play turn-based games against another user's
 * agent, refereed by the central game server. The lobby connects the node and
 * starts/joins tables; the board renders the live game. See docs/modules/games.mdx.
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
      // The arcade cockpit as regions: harness/ladder/challenges on the right
      // strip, the live board on the bottom strip (revealed when a match starts).
      regions: [
        { id: 'games.roster', label: 'Players', icon: '🧑‍🤝‍🧑', key: 'r', position: 'right' },
        { id: 'games.loadout', label: 'Agent Harness', icon: '🛠', key: 'h', position: 'right' },
        { id: 'games.leaderboard', label: 'Ladder', icon: '🏆', key: 'l', position: 'right' },
        { id: 'games.challenges', label: 'Challenges', icon: '🎯', key: 'c', position: 'right' },
        {
          id: 'games.board',
          label: 'Game Board',
          icon: '▦',
          key: 'v',
          position: 'bottom',
          defaultSize: 340,
        },
      ],
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
      role: 'tool',
      icon: '🛠',
      defaultDock: 'left',
      singleton: true,
    },
    {
      id: 'games.leaderboard',
      title: 'Ladder',
      component: LeaderboardPanel,
      role: 'tool',
      icon: '🏆',
      defaultDock: 'right',
      singleton: true,
    },
    {
      id: 'games.challenges',
      title: 'Challenges',
      component: ChallengesPanel,
      role: 'tool',
      icon: '🎯',
      defaultDock: 'right',
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
    {
      id: 'games.roster',
      title: 'Players',
      component: RosterPanel,
      role: 'tool',
      icon: '🧑‍🤝‍🧑',
      defaultDock: 'right',
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
      id: 'games.openLeaderboard',
      title: 'Games: Open ladder',
      run: () => revealRegionView('games.leaderboard'),
    },
    {
      id: 'games.openChallenges',
      title: 'Games: Open challenge track',
      run: () => revealRegionView('games.challenges'),
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
      title: 'Games: Open players roster',
      run: () => revealRegionView('games.roster'),
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
      key: 'games.policy',
      title: 'Move policy',
      description:
        'How this node picks moves: random, agent (local model), or manual (agent tool / UI).',
      type: 'enum',
      default: 'random',
      enumValues: ['random', 'agent', 'manual'],
    },
  ],
};
