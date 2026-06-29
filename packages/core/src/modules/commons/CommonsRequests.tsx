import { useEffect, useSyncExternalStore } from 'react';

import {
  commonsRefresh,
  commonsRespond,
  getCommonsState,
  initCommons,
  subscribeCommons,
} from './commons';

function useCommons() {
  return useSyncExternalStore(subscribeCommons, getCommonsState, getCommonsState);
}

/**
 * Inbound meet requests — the consent inbox. A request does nothing until you Accept
 * (which establishes the peer link) or Decline. See docs/modules/commons.mdx.
 */
export function CommonsRequests() {
  const { requests } = useCommons();

  useEffect(() => {
    initCommons();
    commonsRefresh();
  }, []);

  return (
    <div
      className="commons-requests"
      style={{
        padding: '1rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        height: '100%',
        overflow: 'auto',
      }}
    >
      <h3 style={{ margin: 0 }}>Requests to meet ({requests.length})</h3>
      {requests.length === 0 ? (
        <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>
          No pending requests. When someone asks to meet you, it shows up here.
        </p>
      ) : (
        <ul
          style={{
            listStyle: 'none',
            margin: 0,
            padding: 0,
            display: 'flex',
            flexDirection: 'column',
            gap: '0.4rem',
          }}
        >
          {requests.map((r) => (
            <li
              key={r.request_id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                fontSize: '0.85rem',
                padding: '0.4rem 0.6rem',
                border: '1px solid var(--border, #2a2a2a)',
                borderRadius: 6,
              }}
            >
              <span>
                <strong>{r.from.display_name ?? r.from.node_id}</strong>
                {r.note ? ` — “${r.note}”` : ' wants to meet'}
              </span>
              <button
                style={{ marginLeft: 'auto' }}
                onClick={() => commonsRespond(r.request_id, true)}
              >
                Accept
              </button>
              <button onClick={() => commonsRespond(r.request_id, false)}>Decline</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
