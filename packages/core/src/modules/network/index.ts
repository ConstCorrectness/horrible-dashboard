import { registry, type ModuleManifest } from '../../registry';
import { LobbyPanel } from './LobbyPanel';
import { PeersWidget } from './PeersWidget';
import { initLobby } from './lobby';
import { initNetwork } from './ws';

/**
 * The distributed peer fabric, frontend side: a Peers widget showing presence over
 * the `/ws` `network` channel, plus the settings that configure identity, transport,
 * and trust. Agent-to-agent tools (`list_peers`, `agent.ask_peer`) are backend-static
 * and need no frontend handler. See docs/modules/network.mdx.
 */
export const networkModule: ModuleManifest = {
  id: 'network',
  title: 'Network',
  widgets: [
    {
      id: 'network.peers',
      title: 'Peers',
      component: PeersWidget,
      defaultPlacement: 'right',
    },
    {
      id: 'network.lobby',
      title: 'Lobby',
      component: LobbyPanel,
      defaultPlacement: 'right',
    },
  ],
  commands: [
    {
      id: 'network.open',
      title: 'Network: Open peers',
      run: () => registry.openPanel('network.peers'),
    },
    {
      id: 'network.openLobby',
      title: 'Network: Open lobby',
      run: () => registry.openPanel('network.lobby'),
    },
  ],
  settings: [
    {
      key: 'network.nodeName',
      title: 'Node name',
      description: 'Display name advertised to peers (defaults to the hostname).',
      type: 'string',
      default: '',
    },
    {
      key: 'network.advertisedAddress',
      title: 'Advertised address',
      description: 'The ws://…/peer-ws URL peers should dial to reach this node.',
      type: 'string',
      default: 'ws://localhost:8000/peer-ws',
    },
    {
      key: 'network.enableDirect',
      title: 'Enable direct connections',
      description: 'Accept and dial direct peer-to-peer WebSocket connections.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'network.enableLanDiscovery',
      title: 'Enable LAN discovery',
      description: 'Advertise and discover peers on the local network via mDNS.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'network.relayUrl',
      title: 'Relay URL',
      description: 'Rendezvous broker WebSocket URL for discovery / NAT traversal (blank = off).',
      type: 'string',
      default: '',
    },
    {
      key: 'network.trustMode',
      title: 'Trust mode',
      description: 'Who may pair: manual invite, a directory service, or open on the LAN.',
      type: 'enum',
      default: 'manual',
      enumValues: ['manual', 'directory', 'open-lan'],
    },
    {
      key: 'network.directoryUrl',
      title: 'Directory service URL',
      description: 'Optional directory to find other people by name (blank = off).',
      type: 'string',
      default: '',
    },
    {
      key: 'network.lobbyUrl',
      title: 'Lobby URL',
      description:
        'Lobby server ws://…/lobby-ws for discovery + rooms (blank = off). Joining a room hands off to direct P2P with relay fallback.',
      type: 'string',
      default: '',
    },
    {
      key: 'network.iceEnabled',
      title: 'ICE candidate gathering',
      description:
        'Gather a STUN server-reflexive candidate (public IP) in addition to LAN/host candidates, so peers can try more paths before relaying.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'network.stunServer',
      title: 'STUN server',
      description: 'host:port used to discover this node’s public IP (when ICE is on).',
      type: 'string',
      default: 'stun.l.google.com:19302',
    },
    {
      key: 'network.allowRemoteAgent',
      title: 'Allow remote agents',
      description: "Let a trusted peer's agent ask yours questions.",
      type: 'boolean',
      default: false,
    },
    {
      key: 'network.remoteAgentMode',
      title: 'Remote agent permission mode',
      description:
        'How a remote agent turn is gated. plan = read-only (answer, never act); higher modes allow actions.',
      type: 'enum',
      default: 'plan',
      enumValues: ['plan', 'acceptEdits', 'ask', 'autonomous'],
    },
  ],
};

export { initNetwork };
export { initLobby };
export { subscribeCollab, collabJoin, collabLeave, collabOp, type CollabUpdate } from './collab';
export * from './api';
