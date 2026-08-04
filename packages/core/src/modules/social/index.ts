import { type ModuleManifest } from '../../registry';
import { FriendsPanel } from './FriendsPanel';
import { initSocial } from './ws';

/**
 * The social layer, frontend side: a Friends roster keyed by *person* rather than
 * by machine, with live presence over the `/ws` `social` channel.
 *
 * It contributes **no panes of its own**: the roster is the Friends section of the
 * People pane (`people.home`), so there is one place people live rather than one
 * per module. What stays here is the API client, the `/ws` channel, and the
 * `FriendsPanel` component that pane composes. See docs/modules/social.mdx.
 */
export const socialModule: ModuleManifest = {
  id: 'social',
  title: 'Friends',
  // No settings: your display name is part of your *identity*, not configuration —
  // it is persisted with the person key and edited from the panel, so it can't drift
  // from what the certificates you hand out actually say.
};

export { FriendsPanel };
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
