/**
 * Everything bound for X, LinkedIn and YouTube, across sites: drafts waiting on a
 * person (the agent's among them), what is approved, scheduled, going out, sent and
 * failed. A row opens in its page's Share pane; failed rows can be retried here.
 *
 * Counts per status are rolling counters (`viz/RollingCounter`), seeded at their value
 * so a background tab never shows a wrong number. Live: the runner's `outbox.changed`
 * events refresh the list and show progress on the row being sent.
 */
import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';

import { openExternal } from '../../../external';
import { RollingCounter } from '../../../viz';
import { subscribeChannel } from '../../../ws';
import {
  listOutbox,
  retryOutbox,
  SCRIVE_CHANNEL,
  sendOutbox,
  type OutboxChanged,
  type OutboxItem,
  type OutboxStatus,
} from '../api';
import { ExternalIcon, RefreshIcon } from '../icons';
import { openShare } from '../open';
import { TargetIcon, TARGET_LABEL, when } from './outbox-ui';
import '../scrive.css';

const GROUPS: { status: OutboxStatus; label: string }[] = [
  { status: 'draft', label: 'Drafts' },
  { status: 'failed', label: 'Failed' },
  { status: 'sending', label: 'Sending' },
  { status: 'scheduled', label: 'Scheduled' },
  { status: 'approved', label: 'Approved' },
  { status: 'sent', label: 'Sent' },
];

function summary(item: OutboxItem): string {
  const p = item.payload as { posts?: { text: string }[]; text?: string; title?: string };
  const text = p.posts?.[0]?.text ?? (item.target === 'youtube' ? p.title : p.text) ?? '';
  return text.replace(/\s+/g, ' ').trim() || '(empty)';
}

export function OutboxPanel() {
  const [items, setItems] = useState<OutboxItem[]>([]);
  const [live, setLive] = useState<Record<string, OutboxChanged>>({});
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listOutbox().then(
      (rows) => {
        setItems(rows);
        setError(null);
      },
      (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
    );
  }, []);

  useEffect(refresh, [refresh]);
  useEffect(
    () =>
      subscribeChannel(SCRIVE_CHANNEL, (msg) => {
        if (msg.event !== 'outbox.changed') return;
        const event = msg.data as OutboxChanged;
        setLive((l) => ({ ...l, [event.id]: event }));
        if (event.label === undefined) refresh();
      }),
    [refresh],
  );

  const grouped = useMemo(
    () => GROUPS.map((g) => ({ ...g, rows: items.filter((i) => i.status === g.status) })),
    [items],
  );

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="scrive-publish">
      <header className="scrive-outline-bar">
        <div style={{ minWidth: 0 }}>
          <div className="scrive-head">Outbox</div>
          <div className="scrive-meta">posts bound for X, LinkedIn and YouTube</div>
        </div>
        <button
          type="button"
          className="btn-mini"
          aria-label="Refresh"
          title="Refresh"
          onClick={refresh}
        >
          <RefreshIcon size={12} />
        </button>
      </header>
      <div className="scrive-outbox-counts" role="group" aria-label="Posts by status">
        {grouped.map((g) => (
          <div key={g.status} className="scrive-outbox-count" data-status={g.status}>
            <RollingCounter value={g.rows.length} className="scrive-outbox-count-n" />
            <span className="scrive-head">{g.label}</span>
          </div>
        ))}
      </div>
      {error && (
        <div className="scrive-publish-error" role="alert">
          {error}
        </div>
      )}
      <div className="scrive-outbox-body">
        {items.length === 0 && (
          <p className="scrive-meta">
            Nothing here yet. Drafts appear when you start one from a page's Share pane, or when the
            agent drafts one.
          </p>
        )}
        {grouped
          .filter((g) => g.rows.length)
          .map((g) => (
            <section key={g.status} className="scrive-publish-section">
              <div className="scrive-publish-section-head">
                <span className="scrive-head">{g.label}</span>
                <span className="scrive-meta">{g.rows.length}</span>
              </div>
              <ul className="scrive-publish-pages">
                {g.rows.map((item, i) => {
                  const event = live[item.id];
                  return (
                    <li
                      key={item.id}
                      className="scrive-publish-page"
                      style={{ '--i': Math.min(i, 10) } as CSSProperties}
                    >
                      <button
                        type="button"
                        className="scrive-row"
                        title={`${item.site}/${item.page}`}
                        onClick={() => openShare(item.site, item.page)}
                      >
                        <TargetIcon target={item.target} />
                        <span
                          style={{
                            flex: 1,
                            minWidth: 0,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {summary(item)}
                        </span>
                        {item.created_by === 'agent' && <span className="scrive-chip">agent</span>}
                        <span className="scrive-meta">
                          {item.status === 'sending' && event?.label
                            ? `${event.label}${typeof event.progress === 'number' ? ` ${Math.round(event.progress * 100)}%` : ''}`
                            : item.status === 'scheduled' && item.run_at
                              ? when(item.run_at)
                              : item.status === 'sent' && item.sent_at
                                ? when(item.sent_at)
                                : `${item.site} · ${TARGET_LABEL[item.target]}`}
                        </span>
                      </button>
                      {item.status === 'failed' && (
                        <button
                          type="button"
                          className="scrive-publish-include"
                          title={item.error}
                          onClick={() => void act(() => retryOutbox(item.id))}
                        >
                          retry
                        </button>
                      )}
                      {item.status === 'approved' && (
                        <button
                          type="button"
                          className="scrive-publish-include"
                          onClick={() => void act(() => sendOutbox(item.id))}
                        >
                          send
                        </button>
                      )}
                      {item.status === 'sent' && item.url && (
                        <button
                          type="button"
                          className="scrive-publish-include"
                          aria-label="Open the post"
                          onClick={() => void openExternal(item.url)}
                        >
                          <ExternalIcon size={11} />
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
      </div>
    </div>
  );
}
