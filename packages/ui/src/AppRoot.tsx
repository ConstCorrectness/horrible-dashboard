/**
 * The React root: the boot splash until the app is actually ready, then the
 * shell.
 *
 * The gate is not cosmetic. `AppShell` installs the frame engine on mount, and
 * that hydrates the active workspace — which **prunes panes whose views are not
 * registered**. Mounting the shell before `boot()` has registered its modules
 * and awaited `loadPlugins()` would therefore not just look wrong, it would
 * silently delete the user's layout and then save the result. So the shell
 * mounts once, when boot says so.
 */
import { useSyncExternalStore } from 'react';
import { bootStore } from '@horrible/core';

import { AppShell } from './AppShell';
import { BootSplash } from './desktop/BootSplash';

export function AppRoot({
  appTitle,
  initialWorkspaceId,
}: {
  appTitle: string;
  initialWorkspaceId?: string;
}) {
  const { phase } = useSyncExternalStore(bootStore.subscribe, bootStore.getSnapshot);
  if (phase !== 'ready') return <BootSplash />;
  return <AppShell appTitle={appTitle} initialWorkspaceId={initialWorkspaceId} />;
}
