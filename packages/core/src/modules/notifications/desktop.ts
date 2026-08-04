/**
 * OS-level notifications.
 *
 * `notifyDesktop` in `agent/approvals.ts` has always checked
 * `Notification.permission === 'granted'` — and `Notification.requestPermission()`
 * appeared **nowhere in the repo**, so on a fresh profile the permission is
 * `'default'` forever and that branch is a permanent no-op. This module is where
 * the permission is actually asked for.
 *
 * It is asked for **lazily, on the first notification the user has opted into**,
 * not at boot. A permission prompt on page load is the one every browser vendor
 * tells you not to show and every user reflexively blocks — and a `denied` answer
 * is sticky, so a badly-timed ask permanently costs the feature.
 */
import { hasCapability } from '../../capabilities';
import { getSetting } from '../../settings';

/** Whether this host can show an OS notification at all. */
export function canNotifyDesktop(): boolean {
  return hasCapability('notifications.system') && typeof Notification !== 'undefined';
}

export type PermissionState = 'unsupported' | 'default' | 'granted' | 'denied';

export function desktopPermission(): PermissionState {
  if (!canNotifyDesktop()) return 'unsupported';
  return Notification.permission as PermissionState;
}

let asking: Promise<PermissionState> | null = null;

/**
 * Ask for OS-notification permission, once. Concurrent callers share the same
 * prompt: a second `requestPermission()` while one is open resolves against the
 * same dialog on some browsers and throws on others, and either way two prompts
 * for one decision is a bug the user sees.
 */
export async function ensureDesktopPermission(): Promise<PermissionState> {
  const current = desktopPermission();
  if (current !== 'default') return current;
  if (asking) return asking;
  asking = (async () => {
    try {
      return (await Notification.requestPermission()) as PermissionState;
    } catch {
      return 'denied';
    } finally {
      asking = null;
    }
  })();
  return asking;
}

/**
 * Show an OS notification if the user wants them and the permission allows it.
 * Best-effort by design — the in-app toast is the notification that always lands,
 * and this is the one that reaches you with the window behind something else.
 */
export async function showDesktopNotification(title: string, body: string): Promise<boolean> {
  if (!canNotifyDesktop()) return false;
  if (getSetting<boolean>('notifications.desktop') === false) return false;
  if ((await ensureDesktopPermission()) !== 'granted') return false;
  try {
    new Notification(title, { body });
    return true;
  } catch {
    return false;
  }
}
