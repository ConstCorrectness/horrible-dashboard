import { registry, type ModuleManifest } from '../../registry';
import { ChallengesPanel } from './panels/ChallengesPanel';
import { GameBoardPanel } from './panels/GameBoardPanel';
import { LeaderboardPanel } from './panels/LeaderboardPanel';
import { LoadoutPanel } from './panels/LoadoutPanel';
import { LobbyPanel } from './panels/LobbyPanel';

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
      run: () => registry.openPanel('games.board'),
    },
    {
      id: 'games.openLoadout',
      title: 'Games: Edit agent harness',
      run: () => registry.openPanel('games.loadout'),
    },
    {
      id: 'games.openLeaderboard',
      title: 'Games: Open ladder',
      run: () => registry.openPanel('games.leaderboard'),
    },
    {
      id: 'games.openChallenges',
      title: 'Games: Open challenge track',
      run: () => registry.openPanel('games.challenges'),
    },
  ],
  settings: [
    {
      key: 'games.serverUrl',
      title: 'Game server URL',
      description: 'WebSocket URL of the central game server.',
      type: 'string',
      default: 'ws://localhost:9200',
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
