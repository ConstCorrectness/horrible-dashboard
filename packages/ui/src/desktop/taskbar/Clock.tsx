/**
 * The clock. Locale-formatted, so it says what the user's OS would say rather
 * than imposing a 12/24-hour choice the app has no business making.
 */
import { useEffect, useState } from 'react';

export function Clock({ showLabels }: { showLabels: boolean }) {
  const [now, setNow] = useState(() => new Date());
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
    <div className="os-taskbar-clock" title={now.toLocaleString()}>
      <time dateTime={now.toISOString()}>{time}</time>
      {showLabels && <span className="os-taskbar-date">{date}</span>}
    </div>
  );
}
