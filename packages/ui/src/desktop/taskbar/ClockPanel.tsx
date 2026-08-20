/**
 * The clock flyout: the "what is going on right now" corner.
 *
 * Every OS puts a calendar behind its clock, and this app had nowhere at all for
 * the three other things that answer the same question — what has happened
 * (notifications), what your agent can reach (integrations), and who is around
 * (friends). All four are read-only glances over stores that already exist;
 * nothing here owns state, fetches, or a second copy of a flow that lives
 * elsewhere. Where a section needs an *action*, it hands off to the surface that
 * already owns it rather than growing its own.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import {
  dismissNotification,
  getNotifications,
  getSocialState,
  markAllRead,
  requestConnect,
  revealSection,
  subscribeNotifications,
  subscribeSocial,
  useConnectors,
} from '@horrible/core';

import { ConnectorIcon } from '../../home/connector-icons';

export function ClockPanel({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Opening the panel IS reading the feed — the unread dot on the clock is a
    // "look here", and it would be a liar if it survived you looking.
    markAllRead();
  }, []);

  useEffect(() => {
    // Pointerdown, not click: a click listener fires after the clock's own
    // onClick has already toggled `open` back on, so the panel would reopen
    // instead of closing when you click the clock a second time. Same reason as
    // the start menu's.
    const onDown = (e: PointerEvent) => {
      if (
        !ref.current?.contains(e.target as Node) &&
        !(e.target as HTMLElement).closest('.os-taskbar-clock')
      ) {
        onClose();
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  return (
    <div className="os-clock-panel" ref={ref} role="dialog" aria-label="Status">
      <CalendarSection />
      <NotificationsSection />
      <IntegrationsSection onClose={onClose} />
      <FriendsSection onClose={onClose} />
    </div>
  );
}

// --- calendar ---------------------------------------------------------------

/**
 * The weekday a week starts on, 0=Sunday..6=Saturday.
 *
 * Asked of the locale rather than hardcoded, because Sunday-first is right in the
 * US and wrong across most of Europe — and getting it wrong does not look like a
 * setting, it looks like every date is in the wrong column.
 */
function firstWeekday(): number {
  const loc = new Intl.Locale(navigator.language) as Intl.Locale & {
    weekInfo?: { firstDay: number };
    getWeekInfo?: () => { firstDay: number };
  };
  // Not in every engine yet; Sunday is the safe fallback because it is what the
  // grid did before there was a choice at all.
  const info = loc.getWeekInfo?.() ?? loc.weekInfo;
  // `firstDay` is 1=Monday..7=Sunday; `Date.getDay()` is 0=Sunday..6=Saturday.
  return info ? info.firstDay % 7 : 0;
}

function CalendarSection() {
  const today = useMemo(() => new Date(), []);
  const [month, setMonth] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1));

  const start = useMemo(() => firstWeekday(), []);
  const weekdays = useMemo(() => {
    // Derived from a known week rather than a hardcoded list, so the initials
    // come from the same locale the clock's own date line does.
    const sunday = new Date(2024, 0, 7);
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(sunday);
      d.setDate(sunday.getDate() + ((start + i) % 7));
      return d.toLocaleDateString(undefined, { weekday: 'narrow' });
    });
  }, [start]);

  const cells = useMemo(() => {
    const firstOfMonth = new Date(month.getFullYear(), month.getMonth(), 1);
    const lead = (firstOfMonth.getDay() - start + 7) % 7;
    // Day 0 of the next month is the last day of this one.
    const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
    return [
      ...Array.from({ length: lead }, () => null),
      ...Array.from({ length: days }, (_, i) => i + 1),
    ];
  }, [month, start]);

  const isThisMonth =
    month.getFullYear() === today.getFullYear() && month.getMonth() === today.getMonth();
  const shift = (by: number) => setMonth((m) => new Date(m.getFullYear(), m.getMonth() + by, 1));

  return (
    <section className="os-clock-section">
      <header className="os-clock-cal-head">
        <button type="button" aria-label="Previous month" onClick={() => shift(-1)}>
          ‹
        </button>
        <h3>{month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}</h3>
        <button type="button" aria-label="Next month" onClick={() => shift(1)}>
          ›
        </button>
      </header>
      <div className="os-clock-cal-grid">
        {weekdays.map((w, i) => (
          <span key={`wd${i}`} className="os-clock-cal-wd" aria-hidden="true">
            {w}
          </span>
        ))}
        {cells.map((day, i) =>
          day === null ? (
            <span key={`pad${i}`} />
          ) : (
            <span
              key={day}
              className={`os-clock-cal-day${
                isThisMonth && day === today.getDate() ? ' is-today' : ''
              }`}
              aria-current={isThisMonth && day === today.getDate() ? 'date' : undefined}
            >
              {day}
            </span>
          ),
        )}
      </div>
    </section>
  );
}

// --- notifications ----------------------------------------------------------

function NotificationsSection() {
  const items = useSyncExternalStore(subscribeNotifications, getNotifications, getNotifications);
  return (
    <section className="os-clock-section">
      <h3 className="os-clock-head">Notifications</h3>
      {items.length === 0 ? (
        <p className="os-clock-empty">Nothing new.</p>
      ) : (
        <ul className="os-clock-list">
          {/* A tail, not a log: the store keeps 50 and the backend holds anything
              durable, so a flyout showing every one of them would scroll past the
              thing you opened it to see. */}
          {items.slice(0, 8).map((n) => (
            <li key={n.id} className={`os-clock-note is-${n.kind}`}>
              <span className="os-clock-note-body">
                <strong>{n.title}</strong>
                {n.body && <span>{n.body}</span>}
              </span>
              <button
                type="button"
                className="os-clock-dismiss"
                aria-label={`Dismiss ${n.title}`}
                onClick={() => dismissNotification(n.id)}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// --- integrations -----------------------------------------------------------

function IntegrationsSection({ onClose }: { onClose: () => void }) {
  const { connectors, phase } = useConnectors();
  // Backend down: the other sections still say something useful, and a list that
  // cannot know its own state says nothing at all.
  if (phase === 'unavailable' || connectors.length === 0) return null;
  return (
    <section className="os-clock-section">
      <h3 className="os-clock-head">Integrations</h3>
      <div className="os-clock-connectors">
        {connectors.map((c) => (
          <button
            key={c.id}
            type="button"
            className={`os-clock-connector${c.connected ? ' is-connected' : ''}`}
            title={
              c.connected ? `${c.label} — ${c.account?.label ?? 'connected'}` : `Connect ${c.label}`
            }
            // Still not a connect flow of its own — `ConnectorPopover` is the only
            // one — but it no longer navigates to find it. This used to run
            // `shell.setup`, which switched the backdrop to the splash/home surface
            // and dropped the connector id, so you arrived at the greeting with
            // nothing open. `requestConnect` opens that same popover in the shell
            // dialog, over whatever desktop you were already on.
            onClick={() => {
              requestConnect(c.id);
              onClose();
            }}
          >
            <ConnectorIcon icon={c.icon} label={c.label} />
            <span className="os-clock-connector-name">{c.label}</span>
            {c.error ? (
              <span className="os-clock-dot warn" aria-label="Needs attention" />
            ) : c.connected ? (
              <span className="os-clock-dot ok" aria-hidden="true" />
            ) : null}
          </button>
        ))}
      </div>
    </section>
  );
}

// --- friends ----------------------------------------------------------------

function FriendsSection({ onClose }: { onClose: () => void }) {
  const { roster } = useSyncExternalStore(subscribeSocial, getSocialState, getSocialState);
  // `is_self` filtered out: your own linked machines sit in the roster, and
  // listing yourself as a friend who is online is not news.
  const online = (roster?.friends ?? []).filter((f) => !f.is_self && f.presence === 'online');
  return (
    <section className="os-clock-section">
      {/* The heading is the way through to the real panel. This list is a glance —
          messaging, requests and profiles all live in `people.home`, and half of
          them here would be a second Friends panel that could not do the rest. */}
      <button
        type="button"
        className="os-clock-head is-link"
        onClick={() => {
          revealSection('friends', 'people.home');
          onClose();
        }}
      >
        Friends
        <span className="os-clock-count">{online.length} online</span>
      </button>
      {online.length === 0 ? (
        <p className="os-clock-empty">Nobody is about.</p>
      ) : (
        <ul className="os-clock-list">
          {online.slice(0, 6).map((f) => (
            <li key={f.person_id} className="os-clock-friend">
              <span className="os-clock-dot ok" aria-hidden="true" />
              <span>{f.display_name}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
