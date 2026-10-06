import { hubAccount, signOutHub } from '../../hosted';
import type { ModuleManifest } from '../../registry';
import { toastsStore } from '../../toasts';

/**
 * Hosted: the session a browser holds on the hosted hub (`backend/hub/`).
 *
 * Registered only behind a hub — the web entry checks `isHosted()` — so a local
 * or desktop dashboard never offers to sign out of something it is not signed in
 * to. No pane: the account is not a place you go. See
 * docs/architecture/hosted-hub.mdx.
 */
export const hostedModule: ModuleManifest = {
  id: 'hosted',
  title: 'Hosted account',
  category: 'system',
  commands: [
    {
      id: 'hosted.signOut',
      title: 'Account: Sign out of this dashboard',
      run: () => void signOutHub(),
    },
    {
      id: 'hosted.whoami',
      title: 'Account: Show who is signed in',
      run: () => {
        const account = hubAccount();
        toastsStore.add(
          'info',
          'Hosted account',
          account
            ? `Signed in as ${account.handle ? `@${account.handle}` : account.display_name}`
            : 'Not signed in',
        );
      },
    },
  ],
};
