import type { ModuleManifest } from '../../registry';
import { UpdatesSection } from './UpdatesSection';

/**
 * Updates: the app keeping itself current on someone else's machine.
 *
 * Like `hardware`, this contributes no pane — an update is not a place you go.
 * It is one setting (which channel) plus a section that can check and install,
 * both of which only mean anything under the desktop shell. See
 * docs/architecture/releases.mdx.
 */
export const updatesModule: ModuleManifest = {
  id: 'updates',
  title: 'Updates',
  settings: [
    {
      key: 'app.releaseChannel',
      title: 'Release channel',
      description:
        'Which signed manifest to follow. "stable" is the published release; "beta" gets builds earlier and breaks more. Switching to stable from a newer beta does not downgrade you — it simply finds nothing until stable catches up.',
      type: 'enum',
      enumValues: ['stable', 'beta'],
      default: 'stable',
    },
  ],
  settingsSections: [{ id: 'updates.status', title: 'Updates', component: UpdatesSection }],
};

export * from './api';
