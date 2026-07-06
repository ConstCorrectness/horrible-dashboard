import { registry, type ModuleManifest, type PanelGroupDecl } from '../../registry';
import './games.css';
import { ChallengesPanel } from './panels/ChallengesPanel';
import { GameBoardPanel } from './panels/GameBoardPanel';
import { LeaderboardPanel } from './panels/LeaderboardPanel';
import { LoadoutPanel } from './panels/LoadoutPanel';
import { LobbyPanel } from './panels/LobbyPanel';
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
      defaultPlacement: 'center',
      singleton: true,
    },
    {
      id: 'games.board',
      title: 'Game Board',
      component: GameBoardPanel,
      defaultPlacement: 'center',
      singleton: true,
    },
    {
      id: 'games.loadout',
      title: 'Agent Harness',
      component: LoadoutPanel,
      defaultPlacement: 'center',
      singleton: true,
    },
    {
      id: 'games.leaderboard',
      title: 'Ladder',
      component: LeaderboardPanel,
      defaultPlacement: 'right',
      singleton: true,
    },
    {
      id: 'games.challenges',
      title: 'Challenges',
      component: ChallengesPanel,
      defaultPlacement: 'right',
      singleton: true,
    },
    {
      id: 'games.town',
      title: 'AgentTown',
      component: TownPanel,
      defaultPlacement: 'center',
      singleton: true,
    },
  ],
  // Games, Game Board, Agent Harness, Ladder, and Challenges are one **panel group**:
  // the lobby ("Games") is the hub, the rest are companions that dock inside its
  // shell (see docs/architecture/panel-groups.mdx). The Game Board is revealed
  // automatically when a match starts (game-ws `revealBoard`).
  panelGroups: [
    {
      id: 'games.arcade',
      label: 'Games',
      primary: 'games.lobby',
      companions: [
        { id: 'games.board', label: 'Game Board', icon: '▦' },
        { id: 'games.loadout', label: 'Agent Harness', icon: '🛠' },
        { id: 'games.leaderboard', label: 'Ladder', icon: '🏆' },
        { id: 'games.challenges', label: 'Challenges', icon: '🎯' },
        { id: 'games.town', label: 'AgentTown', icon: '🏘' },
      ],
    } satisfies PanelGroupDecl,
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
      run: () => registry.revealCompanion('games.board'),
    },
    {
      id: 'games.openLoadout',
      title: 'Games: Edit agent harness',
      run: () => registry.revealCompanion('games.loadout'),
    },
    {
      id: 'games.openLeaderboard',
      title: 'Games: Open ladder',
      run: () => registry.revealCompanion('games.leaderboard'),
    },
    {
      id: 'games.openChallenges',
      title: 'Games: Open challenge track',
      run: () => registry.revealCompanion('games.challenges'),
    },
    {
      id: 'games.openTown',
      title: 'Games: Visit AgentTown',
      run: () => registry.revealCompanion('games.town'),
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
