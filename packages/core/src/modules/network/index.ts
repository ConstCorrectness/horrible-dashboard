import { registry, type ModuleManifest } from '../../registry';
import { AgentRelayPanel } from './AgentRelayPanel';
import { LobbyPanel } from './LobbyPanel';
import { PeerChatPanel } from './PeerChatPanel';
import { PeerMonitor } from './PeerMonitor';
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
      role: 'tool',
      icon: '⇄',
      defaultDock: 'right',
      // The peer-fabric satellites as regions on the Peers tool.
      regions: [
        { id: 'network.chat', label: 'Peer Chat', icon: '✉', position: 'bottom' },
        { id: 'network.monitor', label: 'Peer Monitor', icon: '◈', position: 'bottom' },
        { id: 'network.lobby', label: 'Lobby', icon: '⊞', position: 'bottom' },
        { id: 'network.relay', label: 'Agent Relay', icon: '⇌', position: 'bottom' },
      ],
    },
    {
      id: 'network.lobby',
      title: 'Lobby',
      component: LobbyPanel,
      role: 'widget',
      icon: '⊞',
    },
    {
      id: 'network.monitor',
      title: 'Peer Monitor',
      component: PeerMonitor,
      role: 'widget',
      icon: '◈',
    },
    {
      id: 'network.chat',
      title: 'Peer Chat',
      component: PeerChatPanel,
      role: 'widget',
      icon: '✉',
    },
    {
      id: 'network.relay',
      title: 'Agent Relay',
      component: AgentRelayPanel,
      role: 'widget',
      icon: '⇌',
    },
  ],
  commands: [
    {
      id: 'network.open',
      title: 'Network: Open peers',
      run: () => registry.openPanel('network.peers'),
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
      description:
        'The ws://…/peer-ws URL peers should dial to reach this node, baked into invite QR codes. Blank auto-detects this machine’s LAN address; set it only for a public hostname or a forwarded port.',
      type: 'string',
      default: '',
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
      key: 'network.enableWebRtc',
      title: 'Enable WebRTC transport',
      description:
        'Connect to peers over a WebRTC data channel (ICE/STUN NAT traversal), with SDP signaling via the lobby. Requires the backend `webrtc` extra (aiortc); the relay stays the fallback.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'network.turnUrl',
      title: 'TURN server URL',
      description:
        'Optional TURN relay for WebRTC (e.g. turn:host:3478) used when STUN can’t punch the NAT (blank = STUN only).',
      type: 'string',
      default: '',
    },
    {
      key: 'network.turnUsername',
      title: 'TURN username',
      description: 'Username credential for the TURN server (if it requires auth).',
      type: 'string',
      default: '',
    },
    {
      key: 'network.turnCredential',
      title: 'TURN credential',
      description: 'Password/credential for the TURN server (if it requires auth).',
      type: 'string',
      default: '',
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
export { useCollab, type CollabPane, type UseCollabOptions } from './useCollab';
export {
  subscribeChat,
  chatOpen,
  chatSend,
  chatClose,
  type ChatMessage,
  type ChatEvent,
} from './peerchat';
export * from './api';
