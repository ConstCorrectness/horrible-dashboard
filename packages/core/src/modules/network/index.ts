import { registry, type ModuleManifest } from '../../registry';
import { AgentRelayPanel } from './AgentRelayPanel';
import { PeerChatPanel } from './PeerChatPanel';
import { PeerMonitor } from './PeerMonitor';
import { PeersWidget } from './PeersWidget';
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
      id: 'network.monitor',
      title: 'Peer Monitor',
      component: PeerMonitor,
      defaultPlacement: 'bottom',
    },
    {
      id: 'network.chat',
      title: 'Peer Chat',
      component: PeerChatPanel,
      defaultPlacement: 'right',
    },
    {
      id: 'network.relay',
      title: 'Agent Relay',
      component: AgentRelayPanel,
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
      id: 'network.openMonitor',
      title: 'Network: Open peer monitor',
      run: () => registry.openPanel('network.monitor'),
    },
    {
      id: 'network.openChat',
      title: 'Network: Open peer chat',
      run: () => registry.openPanel('network.chat'),
    },
    {
      id: 'network.openRelay',
      title: 'Network: Ask a peer agent',
      run: () => registry.openPanel('network.relay'),
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
