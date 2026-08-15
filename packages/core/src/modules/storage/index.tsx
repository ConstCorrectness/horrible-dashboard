import type { ModuleManifest } from '../../registry';
import { StorageSection } from './StorageSection';

/**
 * Storage: where this node's files live.
 *
 * No pane and no settings of its own — the locations are not a form, they are a
 * *reading*, resolved by `backend/paths.py` from the environment, the checkout and
 * the OS. The overrides are environment variables rather than settings on purpose:
 * they must be resolvable before anything is read, and `settings.json` is itself in
 * the data dir this decides the location of. Same shape as the hardware module,
 * which is a probe rather than a pane for the same reason.
 *
 * See docs/architecture/data-directories.mdx.
 */
export const storageModule: ModuleManifest = {
  id: 'storage',
  title: 'Storage',
  settingsSections: [{ id: 'storage.locations', title: 'Storage', component: StorageSection }],
};

export * from './api';
