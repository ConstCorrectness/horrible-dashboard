import { registry, type ModuleManifest } from '../../registry';
import { MirrorPanel } from './panels/MirrorPanel';
import { SessionPanel } from './panels/SessionPanel';
import { getLink, revokeLink } from './api';
import { bindPublishing } from './publish';
import { bindStreamLifecycle, stopStream } from './stream';
import {
  initShare as initShareChannel,
  requestShareState,
  revokeAllViaChannel,
  startViaChannel,
  stopViaChannel,
} from './ws';

/**
 * Start the share channel **and** the workspace projector.
 *
 * One entry point rather than two so a caller cannot start the channel and
 * forget the projector, which would leave a host in a session that publishes
 * nothing and no error anywhere to explain it.
 */
export function initShare(): void {
  initShareChannel();
  bindPublishing();
  // A capture must not outlive the session it belongs to — and a session can end
  // from another tab, from the agent, or from the Stop button, none of which the
  // streaming code would otherwise hear about.
  bindStreamLifecycle();
}

/**
 * Share: put another person inside this workspace.
 *
 * Two modes of one module. A **friend on the fabric** gets a semantic session —
 * the workspace mirrored as structured state, with a revocable ladder of rights
 * over it. **Anyone with a link** gets a video stream and a chat box, no install
 * required. This phase builds the session and the permission model; the media
 * layers land on top of it.
 *
 * The pane is a permission surface, not a video one, and that ordering is
 * deliberate: what has to be right before any pixels move is that a host can see
 * every rung at a glance and take all of them away in one click.
 *
 * See docs/modules/share.mdx.
 */
export const shareModule: ModuleManifest = {
  id: 'share',
  title: 'Share',
  panels: [
    {
      id: 'share.session',
      title: 'Share',
      component: SessionPanel,
      role: 'tool',
      // The rail glyph is a manifest string and follows the rail's convention;
      // iconography *inside* the pane is vector-only.
      icon: '🖧',
      singleton: true,
      // `role: 'tool'` already makes it dockable; `defaultDock` names the side.
      defaultDock: 'right',
      defaultDockSize: 340,
    },
    {
      id: 'share.mirror',
      title: 'Session mirror',
      component: MirrorPanel,
      role: 'document',
      icon: '👁',
      singleton: true,
    },
  ],
  settings: [
    {
      key: 'share.relayUrl',
      title: 'Share relay URL',
      description:
        'Address of the relay that serves public share links (e.g. https://horrible-share.fly.dev). Blank means this node can only share with friends on the fabric — no public URL. The relay key is env-only (SHARE_RELAY_KEY) and never a setting, because every setting is served to the browser.',
      type: 'string',
      default: '',
      advanced: true,
    },
  ],
  commands: [
    {
      id: 'share.open',
      title: 'Share: Open session panel',
      run: () => {
        requestShareState();
        registry.openPanel('share.session');
      },
    },
    {
      id: 'share.start',
      title: 'Share: Start a session',
      run: () => {
        startViaChannel('');
        registry.openPanel('share.session');
      },
    },
    {
      id: 'share.openMirror',
      title: 'Share: Open the session mirror',
      run: () => {
        requestShareState();
        registry.openPanel('share.mirror');
      },
    },
    {
      id: 'share.stopScreen',
      title: 'Share: Stop sharing my screen',
      run: () => void stopStream(),
    },
    {
      id: 'share.stop',
      title: 'Share: Stop the session',
      run: () => stopViaChannel(),
    },
    {
      id: 'share.copyLink',
      title: 'Share: Copy the public link',
      run: async () => {
        const link = await getLink();
        // Nothing is minted implicitly. A command that silently created a public
        // URL because somebody was looking for one would be the single most
        // surprising thing this module could do.
        if (!link.view_url) return;
        await navigator.clipboard.writeText(link.view_url);
      },
    },
    {
      id: 'share.revokeLink',
      title: 'Share: Revoke the public link',
      run: () => void revokeLink(),
    },
    {
      id: 'share.revokeAll',
      // Phrased as what it does rather than as "revoke all": somebody reaching
      // for this in the palette is reaching for it in a hurry.
      title: 'Share: Drop every guest to view-only',
      run: () => revokeAllViaChannel(),
    },
  ],
};

export { SessionPanel, MirrorPanel };
export * from './mirror';
export { probeCapture, type CaptureSupport } from './capture';
export { WhipPublisher } from './whip';
export { checkPreflight, getStreamState, subscribeStream, type StreamState } from './stream';
export * from './api';
export {
  getShareSnapshot,
  subscribeShare,
  requestShareState,
  onShareSignal,
  sendShareSignal,
} from './ws';
