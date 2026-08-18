/**
 * The clock. Locale-formatted, so it says what the user's OS would say rather
 * than imposing a 12/24-hour choice the app has no business making.
 *
 * It is also the handle for {@link ClockPanel} — the corner every OS files the
 * calendar and the notification feed under. Before that it was an inert `<div>`,
 * which meant the notifications service in core had **no UI consumer at all**:
 * every fired watch and peer invite went to a toast that vanished, and there was
 * nowhere to go and look at what you missed.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';
import { subscribeNotifications, unreadCount } from '@horrible/core';

import { ClockPanel } from './ClockPanel';

export function Clock({ showLabels }: { showLabels: boolean }) {
  const [now, setNow] = useState(() => new Date());
  const [open, setOpen] = useState(false);
  const unread = useSyncExternalStore(subscribeNotifications, unreadCount, unreadCount);

  useEffect(() => {
    // Aligned to the next minute boundary rather than ticking every 60s from
    // mount, so the display changes when the minute does. A 60s interval started
    // at :30 shows every minute half a minute late, permanently.
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      const d = new Date();
      setNow(d);
      timer = setTimeout(schedule, 60_000 - (d.getSeconds() * 1000 + d.getMilliseconds()));
    };
    schedule();
    return () => clearTimeout(timer);
  }, []);

  const time = now.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  const date = now.toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
  return (
    <div className="os-clock">
      <button
        type="button"
        className={`os-taskbar-clock${open ? ' is-open' : ''}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={unread ? `Status — ${unread} unread` : 'Status'}
        title={now.toLocaleString()}
        onClick={() => setOpen((v) => !v)}
      >
        <time dateTime={now.toISOString()}>{time}</time>
        {showLabels && <span className="os-taskbar-date">{date}</span>}
        {/* The dot, not a count: the feed is a glance, and a number implies a
            queue you are expected to work through. */}
        {unread > 0 && <span className="os-clock-unread" aria-hidden="true" />}
      </button>
      {open && <ClockPanel onClose={() => setOpen(false)} />}
    </div>
  );
}
