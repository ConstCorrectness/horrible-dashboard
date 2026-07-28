import { registry, type ModuleManifest } from '../../registry';
import { FriendsPanel } from './FriendsPanel';
import { initSocial } from './ws';

/**
 * The social layer, frontend side: a Friends roster keyed by *person* rather than
 * by machine, with live presence over the `/ws` `social` channel.
 *
 * Where the network module's Peers widget shows the raw fabric (nodes, transports,
 * trust), this shows the human view of it: one row per person, their machines
 * folded underneath. See docs/modules/social.mdx.
 */
export const socialModule: ModuleManifest = {
  id: 'social',
  title: 'Friends',
  widgets: [
    {
      id: 'social.friends',
      title: 'Friends',
      component: FriendsPanel,
      role: 'tool',
      icon: '👥',
      defaultDock: 'right',
    },
  ],
  commands: [
    {
      id: 'social.open',
      title: 'Friends: Open friends list',
      run: () => registry.openPanel('social.friends'),
    },
  ],
  // No settings: your display name is part of your *identity*, not configuration —
  // it is persisted with the person key and edited from the panel, so it can't drift
  // from what the certificates you hand out actually say.
};

export { initSocial };
export * from './api';
export {
  getSocialState,
  subscribeSocial,
  requestRoster,
  respondViaChannel,
  removeViaChannel,
  blockViaChannel,
} from './ws';
