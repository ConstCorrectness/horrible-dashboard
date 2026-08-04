import { type ModuleManifest } from '../../registry';
import { initNotifications } from './ws';

/**
 * Notifications, frontend side. It contributes **settings and a service, no panes**
 * — the feed is read by whatever wants to render it (the People pane's bell), the
 * same way `social` contributes the roster without owning a destination.
 *
 * The *rules* — mutes, per-person exceptions, standing watches — deliberately live
 * on the backend (`backend/modules/notifications/`), not in these settings. A mute
 * is enforced at the producer so a silenced notification is never sent at all, and
 * a rule the agent can write in plain language ("mute everything except Andrew for
 * a bit") has a duration and a scope that no boolean on a settings page could hold.
 * What is here is the two things that are genuinely per-browser: whether this host
 * may raise an OS-level notification, and whether it may do so while you are
 * actively looking at the app. See docs/modules/notifications.mdx.
 */
export const notificationsModule: ModuleManifest = {
  id: 'notifications',
  title: 'Notifications',
  settings: [
    {
      key: 'notifications.desktop',
      title: 'Desktop notifications',
      description:
        'Raise an OS notification as well as an in-app toast. Asks for permission the first time one fires.',
      type: 'boolean',
      default: true,
    },
  ],
};

export { initNotifications };
export {
  getNotifications,
  subscribeNotifications,
  unreadCount,
  markAllRead,
  dismissNotification,
  type NotificationItem,
} from './ws';
export {
  canNotifyDesktop,
  desktopPermission,
  ensureDesktopPermission,
  showDesktopNotification,
  type PermissionState,
} from './desktop';
