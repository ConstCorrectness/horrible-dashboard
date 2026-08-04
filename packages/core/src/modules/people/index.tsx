import { revealSection } from '../../layout/controller';
import { registry, type ModuleManifest } from '../../registry';
import { CommonsRequests } from '../commons';
import { PeerChatPanel } from '../network/PeerChatPanel';
import { FriendsPanel } from '../social/FriendsPanel';
import { DiscoverSection } from './DiscoverSection';
import { MeSection } from './MeSection';
import './people.css';

/**
 * **People** — one pane for everyone you know, replacing nine.
 *
 * What it replaces, and why each one went:
 *
 * | Retired | Where it went |
 * | --- | --- |
 * | `social.friends` | **Friends** |
 * | `network.chat` | **Messages** |
 * | `commons.directory` | **Discover** (under callsign search) |
 * | `commons.requests` | **Requests** |
 * | `commons.profile` | **Me** |
 * | `network.peers` | nowhere — see below |
 * | `network.monitor`, `network.lobby`, `network.relay` | nowhere — see below |
 *
 * The last four were **infrastructure that became destinations**: Peers listed raw
 * fabric nodes, Peer Monitor was a metrics readout, Lobby was a rendezvous client,
 * Agent Relay was a traffic log with two permission toggles. They became panes
 * because a pane was the only way to show anything, and "Peers" in particular was
 * the least intuitive thing in the shell — it answered "which machines are
 * connected" when the question people actually have is "which of my friends is
 * around". That question is what this pane answers, keyed by **person**: one row
 * per human, their machines folded underneath.
 *
 * Their substance did not disappear — the transports, trust store and rendezvous
 * are still there, as backend services (`network/ws.ts`, `lobby.ts`, `peerchat.ts`)
 * with their settings on the settings page and their traffic in observability.
 * What went away is the idea that each of them deserved somewhere to *live*.
 *
 * **One agent-context provider**, per the sections contract: section bodies render
 * inside this pane's instance id, so two providers would silently overwrite each
 * other. See docs/architecture/windowing.mdx.
 */
export const peopleModule: ModuleManifest = {
  id: 'people',
  title: 'People',
  widgets: [
    {
      id: 'people.home',
      title: 'People',
      component: FriendsPanel,
      role: 'tool',
      icon: '👥',
      defaultDock: 'right',
      defaultDockSize: 340,
      sections: [
        { id: 'friends', label: 'Friends', icon: '👥', component: FriendsPanel, default: true },
        { id: 'messages', label: 'Messages', icon: '✉', component: PeerChatPanel, key: 'm' },
        { id: 'discover', label: 'Discover', icon: '🔎', component: DiscoverSection, key: 'd' },
        { id: 'requests', label: 'Requests', icon: '↙', component: CommonsRequests, key: 'r' },
        { id: 'me', label: 'Me', icon: '🪪', component: MeSection },
      ],
    },
  ],
  commands: [
    {
      id: 'people.open',
      title: 'People: Open',
      run: () => registry.openPanel('people.home'),
    },
    {
      id: 'people.find',
      title: 'People: Find someone by callsign',
      run: () => {
        revealSection('discover', 'people.home');
      },
    },
    {
      id: 'people.me',
      title: 'People: My callsign and friend code',
      run: () => {
        revealSection('me', 'people.home');
      },
    },
  ],
};

export { DiscoverSection, MeSection };
