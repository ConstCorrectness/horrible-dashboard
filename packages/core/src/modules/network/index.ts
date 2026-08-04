import { type ModuleManifest } from '../../registry';
import { PeerChatPanel } from './PeerChatPanel';
import { initLobby } from './lobby';
import { initNetwork } from './ws';

/**
 * The distributed peer fabric, frontend side. It contributes **settings and
 * services, no panes**.
 *
 * It used to own five: Peers, Peer Chat, Peer Monitor, Lobby and Agent Relay. Only
 * Peer Chat was something a person wanted to *look at*, and it is now the Messages
 * section of the People pane. The other four were infrastructure readouts that
 * became destinations because a pane was the only way to show anything — "Peers"
 * answered "which machines are connected" when the real question is "which of my
 * friends is around", which People answers by person.
 *
 * Nothing was lost: transports, trust and rendezvous are still here as services
 * (`ws.ts`, `lobby.ts`, `peerchat.ts`, `collab.ts`), the knobs are on the settings
 * page, and the traffic is in observability. Agent-to-agent tools (`list_peers`,
 * `agent.ask_peer`) are backend-static and need no frontend handler.
 * See docs/modules/network.mdx.
 */
export const networkModule: ModuleManifest = {
  id: 'network',
  title: 'Network',
  // Ordered basics-first, then the `advanced: true` block. The settings page
  // filters rather than reading order, so this grouping is for whoever edits this
  // file — but it means the two lists read the same here as they render there.
  //
  // The line between them: a basic setting is one a working install might
  // legitimately change (what am I called, do I accept connections, who may pair).
  // Advanced is NAT-traversal plumbing — TURN credentials, STUN hosts, relay and
  // signalling URLs — which is either irrelevant or copied verbatim from whoever
  // runs your infrastructure. Fourteen knobs at equal weight taught people to skim
  // past the six that matter.
  settings: [
    {
      key: 'network.nodeName',
      title: 'Node name',
      description: 'Display name advertised to peers (defaults to the hostname).',
      type: 'string',
      default: '',
    },
    {
      key: 'network.enableDirect',
      title: 'Enable direct connections',
      description:
        'Accept and dial direct peer-to-peer WebSocket connections. Takes effect on restart.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'network.enableLanDiscovery',
      title: 'Enable LAN discovery',
      description:
        'Advertise and discover peers on the local network via mDNS. Takes effect on restart.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'network.trustMode',
      title: 'Trust mode',
      description:
        'Who may pair: only someone holding an invite you generated (manual), or anyone on your local network (open-lan). Accepting a friend grants trust either way.',
      type: 'enum',
      default: 'manual',
      // `directory` was a third option and it could not work: nothing implemented
      // it, so `trust.evaluate` rejected every peer outright — selecting it made
      // the node silently unpairable. A stored `directory` is read as `manual`.
      enumValues: ['manual', 'open-lan'],
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
      key: 'network.allowRemoteAgent',
      title: 'Allow remote agents',
      description: "Let a trusted peer's agent ask yours questions.",
      type: 'boolean',
      default: false,
    },

    // ---- Advanced: NAT traversal, rendezvous, and remote-agent gating ----------
    {
      key: 'network.advertisedAddress',
      title: 'Advertised address',
      description:
        'The ws://…/peer-ws URL peers should dial to reach this node, baked into invite QR codes. Blank auto-detects this machine’s LAN address; set it only for a public hostname or a forwarded port.',
      type: 'string',
      default: '',
      advanced: true,
    },
    {
      key: 'network.relayUrl',
      title: 'Relay URL',
      description:
        'Rendezvous broker WebSocket URL for discovery / NAT traversal (blank = off). Takes effect on restart.',
      type: 'string',
      default: '',
      advanced: true,
    },
    {
      key: 'network.iceEnabled',
      title: 'ICE candidate gathering',
      description:
        'Gather a STUN server-reflexive candidate (public IP) in addition to LAN/host candidates, so peers can try more paths before relaying.',
      type: 'boolean',
      default: false,
      advanced: true,
    },
    {
      key: 'network.stunServer',
      title: 'STUN server',
      description: 'host:port used to discover this node’s public IP (when ICE is on).',
      type: 'string',
      default: 'stun.l.google.com:19302',
      advanced: true,
    },
    {
      key: 'network.enableWebRtc',
      title: 'Enable WebRTC transport',
      description:
        'Connect to peers over a WebRTC data channel (ICE/STUN NAT traversal), with SDP signaling via the lobby. Requires the backend `webrtc` extra (aiortc); the relay stays the fallback. Takes effect on restart.',
      type: 'boolean',
      default: false,
      advanced: true,
    },
    {
      key: 'network.turnUrl',
      title: 'TURN server URL',
      description:
        'Optional TURN relay for WebRTC (e.g. turn:host:3478) used when STUN can’t punch the NAT (blank = STUN only).',
      type: 'string',
      default: '',
      advanced: true,
    },
    {
      key: 'network.turnUsername',
      title: 'TURN username',
      description: 'Username credential for the TURN server (if it requires auth).',
      type: 'string',
      default: '',
      advanced: true,
    },
    {
      key: 'network.turnCredential',
      title: 'TURN credential',
      description: 'Password/credential for the TURN server (if it requires auth).',
      type: 'string',
      default: '',
      advanced: true,
    },
    {
      key: 'network.remoteAgentMode',
      title: 'Remote agent permission mode',
      description:
        'How a remote agent turn is gated. plan = read-only (answer, never act); higher modes allow actions.',
      type: 'enum',
      default: 'plan',
      // No `ask`: it prompts a human, and the human whose approval it would
      // ask for is not the one who started the turn. It silently ran as
      // `default` instead, which is a different permission level than the name
      // promises.
      enumValues: ['plan', 'acceptEdits', 'autonomous'],
      advanced: true,
    },
  ],
};

export { PeerChatPanel };
export { initNetwork };
export { initLobby };
export {
  subscribeCollab,
  collabJoin,
  collabLeave,
  collabOp,
  collabShare,
  collabUnshare,
  type CollabUpdate,
} from './collab';
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
