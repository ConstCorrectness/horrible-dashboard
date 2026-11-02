/**
 * Follow: the agent chat's turns, watched from the inside.
 *
 * Locks onto the chat pane used most recently (or one turn of it, pinned from a
 * reply's trace button) and shows, for the selected turn, every **provider call**
 * it made, as the provider received it:
 *
 * - **Request** (left): the literal request of the selected call — a minimap of the
 *   window by tokens beside the raw messages. Roles are the wire's four; where a
 *   stretch of text came from (the skill catalog, a tool guide, a nudge) is a tint
 *   inside the message, never a role of its own. See RequestInspector.
 * - **Calls** (right): `R0`, `R1`, `R1 · retry`… each with what it got back — the
 *   answer, the tools it asked for, the reasoning — and, for the selected one, the
 *   tool results that answered it in the next call.
 *
 * What the agent *did*, step by step, is the Trajectories run view's job; this view
 * keeps to one vocabulary, the request's.
 */
import { useContext, useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { PaneInstanceContext } from '../../../agent-context';
import { IconClose, IconExternal, IconMinus, IconPin, IconPinOff } from '../../../glyphs';
import { focusInstance, openPane, revealSection } from '../../../layout/controller';
import { listPanes } from '../../../layout/model';
import { layoutStore } from '../../../layout/store';
import { EmptyState, PaneHeader } from '../../../Primitives';
import {
  chatState,
  setFollowTarget,
  setTraceDrawer,
  subscribeChats,
  useFollowTarget,
  type FollowFocus,
} from '../../agent/chat-state';
import {
  getRequestDetail,
  getTurnRequests,
  loadTurnRequests,
  subscribeRequests,
  type RequestDetail,
  type RequestSummary,
} from '../../interpretability/requests';
import { selectTurn, turnRows, type TurnRow } from './follow-model';
import { RequestInspector } from './RequestInspector';
import { answersIn, reasoningOf, requestLabel, resolveFocus, toolCallsOf } from './request-model';
import { mono } from './common';
import './follow.css';

const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(Math.round(n)));

/** The turn picked in the rail, per agent, and the newest turn at the time. A new
 *  turn starting (a different newest) drops the pick so the view follows it. */
const picks = new Map<string, { turnId: string; newest: string | null }>();

/**
 * `pane`: the Trajectories → Agent section. `drawer`: the same view slid out of the
 * agent chat pane. Each can hand off to the other: the drawer pops out into the
 * pane, and the pane docks back into the chat as the drawer.
 */
export function FollowSection({ mode = 'pane' }: { mode?: 'pane' | 'drawer' }) {
  const instanceId = useContext(PaneInstanceContext);
  const popOut = () => {
    setTraceDrawer({ open: false });
    revealSection('follow', 'trajectories.hub');
  };
  const dockIntoChat = () => {
    setTraceDrawer({ open: true });
    // The chat already open, not a fresh one: `agent.chat` is not a singleton, so
    // opening it would stack another chat window for every dock.
    const chat = listPanes(layoutStore.getSnapshot().frame).find(
      (p) => p.pane.viewId === 'agent.chat',
    );
    if (chat) focusInstance(chat);
    else openPane('agent.chat');
    if (instanceId) layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId });
  };
  const target = useFollowTarget();
  const agentId = target?.agentId ?? 'main';
  const chat = useSyncExternalStore(subscribeChats, () => chatState(agentId));
  const rows = useMemo(() => turnRows(chat.turns, chat.busy), [chat.turns, chat.busy]);

  // A click in the rail overrides "newest" without pinning; the pin holds it. The
  // pick is remembered with the newest turn it was made under, and outside the
  // component: the pane remounts, and the transcript is briefly empty while the
  // chat re-reads its session, and neither is a new turn starting.
  const newestId = rows.find((r) => r.depth === 0)?.turnId ?? null;
  const [, rerender] = useState(0);
  const pick = picks.get(agentId);
  const picked = pick && (newestId === null || pick.newest === newestId) ? pick.turnId : null;
  const setPicked = (id: string) => {
    picks.set(agentId, { turnId: id, newest: newestId });
    rerender((n) => n + 1);
  };
  const pinned = target?.pinnedTurnId ?? null;
  // A pin (set here, or by a reply's trace button) outranks a pick; a rail click
  // while pinned moves the pin rather than hiding under it.
  const turnId = pinned ?? picked ?? selectTurn(rows, null);

  // Pinning and unpinning both start from a clean slate: unpinning means "follow
  // the newest", not "go back to whatever was clicked before the pin".
  const setPinned = (id: string | null) => {
    picks.delete(agentId);
    setFollowTarget(agentId, id);
  };

  return (
    <div className="traj-follow">
      <PaneHeader
        title="Agent"
        meta={[
          <span key="a" style={mono}>
            {agentId}
          </span>,
          <span key="n" style={mono}>
            {rows.filter((r) => r.depth === 0).length} turns
          </span>,
        ]}
        actions={
          <>
            {turnId ? (
              <button
                type="button"
                className="traj-follow-pin"
                aria-pressed={pinned === turnId}
                onClick={() => setPinned(pinned === turnId ? null : turnId)}
                title={pinned === turnId ? 'Unpin: follow the newest turn' : 'Pin this turn'}
              >
                {pinned === turnId ? <IconPin width={13} /> : <IconPinOff width={13} />}
                {pinned === turnId ? 'pinned' : 'following'}
              </button>
            ) : null}
            {mode === 'drawer' ? (
              <>
                <button
                  type="button"
                  className="traj-follow-pin"
                  onClick={popOut}
                  title="Pop out into its own window"
                  aria-label="Pop out"
                >
                  <IconExternal width={13} />
                </button>
                <button
                  type="button"
                  className="traj-follow-pin"
                  onClick={() => setTraceDrawer({ open: false })}
                  title="Collapse into the chat"
                  aria-label="Collapse"
                >
                  <IconClose width={13} />
                </button>
              </>
            ) : (
              <button
                type="button"
                className="traj-follow-pin"
                onClick={dockIntoChat}
                title="Dock into the agent chat"
                aria-label="Dock into chat"
              >
                <IconMinus width={13} />
                dock
              </button>
            )}
          </>
        }
      />
      {rows.length === 0 ? (
        <EmptyState title="No turns yet">Send the agent a message.</EmptyState>
      ) : (
        <div className="traj-follow-grid">
          <TurnRail
            rows={rows}
            selected={turnId}
            onPick={(id) => {
              setPicked(id);
              if (pinned) setPinned(id);
            }}
          />
          {turnId ? (
            <TurnView
              key={turnId}
              turnId={turnId}
              focus={pinned === turnId ? (target?.focus ?? null) : null}
            />
          ) : null}
        </div>
      )}
    </div>
  );
}

function TurnRail({
  rows,
  selected,
  onPick,
}: {
  rows: TurnRow[];
  selected: string | null;
  onPick: (id: string) => void;
}) {
  return (
    <nav className="traj-follow-rail" aria-label="Turns">
      {rows.map((row, i) => (
        <button
          type="button"
          key={row.turnId}
          className="traj-follow-turn traj-in"
          data-depth={row.depth}
          aria-current={row.turnId === selected}
          onClick={() => onPick(row.turnId)}
          style={{ ['--traj-i' as string]: Math.min(i, 8) }}
        >
          <span className="traj-follow-turn-title">
            {row.running ? <span className="traj-follow-pulse" aria-label="running" /> : null}
            {row.depth === 1 ? <span className="traj-follow-sub">↳</span> : null}
            {row.title}
          </span>
          <span className="traj-follow-turn-meta">
            {row.depth === 1 && row.ok === false ? 'failed · ' : ''}
            {row.turnId.split(':').pop()}
          </span>
        </button>
      ))}
    </nav>
  );
}

/** A turn's provider calls: live from the socket, back-filled on open. */
function useTurnRequests(turnId: string): RequestSummary[] | undefined {
  const calls = useSyncExternalStore(subscribeRequests, () =>
    getTurnRequests(turnId),
  );
  useEffect(() => {
    void loadTurnRequests(turnId);
  }, [turnId]);
  return calls;
}

/** One call's bodies, re-read when its status changes (pending → ok). */
function useRequestDetail(
  call: RequestSummary | undefined,
): RequestDetail | null {
  const [detail, setDetail] = useState<RequestDetail | null>(null);
  const id = call?.id;
  const status = call?.status;
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    getRequestDetail(id)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail(null));
    return () => {
      cancelled = true;
    };
  }, [id, status]);
  return detail && detail.id === id ? detail : null;
}

function TurnView({
  turnId,
  focus,
}: {
  turnId: string;
  focus: FollowFocus | null;
}) {
  const calls = useTurnRequests(turnId);
  const list = useMemo(() => calls ?? [], [calls]);
  const [picked, setPicked] = useState<string | null>(null);
  const [jump, setJump] = useState<{ index: number; nonce: number } | null>(
    null,
  );

  // A deep link picks its call once the turn's calls are known, and again for
  // every new nonce: the user may have clicked elsewhere in between.
  const nonce = focus?.nonce;
  const known = list.length;
  useEffect(() => {
    if (!focus || !known) return;
    const id = resolveFocus(list, focus);
    if (id) setPicked(id);
  }, [nonce, known]);

  const at = picked ? list.findIndex((c) => c.id === picked) : -1;
  // No pick follows the newest call, so a running turn stays on its live edge.
  const selIdx = at >= 0 ? at : list.length - 1;
  const selected = list[selIdx];
  const detail = useRequestDetail(selected);
  const prev = useRequestDetail(list[selIdx - 1]);
  const next = useRequestDetail(list[selIdx + 1]);

  if (calls === undefined) {
    return (
      <div className="traj-follow-main">
        <EmptyState title="Loading calls" />
      </div>
    );
  }
  if (!list.length) {
    return (
      <div className="traj-follow-main">
        <EmptyState title="No provider calls recorded">
          A turn that ran on another node, or one that never reached a model.
        </EmptyState>
      </div>
    );
  }

  const pick = (id: string) =>
    setPicked(id === list[list.length - 1].id ? null : id);

  return (
    <div className="traj-follow-main">
      <div className="traj-follow-cols">
        <section className="traj-follow-inspector" aria-label="Request">
          {detail ? (
            <RequestInspector detail={detail} prev={prev} focus={jump} />
          ) : (
            <EmptyState title="Loading request" />
          )}
        </section>
        <section className="traj-follow-rounds" aria-label="Calls">
          <h3 className="traj-follow-h">
            Calls
            <span
              className="traj-follow-h-meta"
              title={selected?.model || undefined}
            >
              {list.length}
              {selected?.model ? ` · ${selected.model}` : ""}
            </span>
          </h3>
          {list.map((call, i) => (
            <CallCard
              key={call.id}
              call={call}
              index={i}
              selected={i === selIdx}
              detail={i === selIdx ? detail : null}
              next={i === selIdx ? next : null}
              onPick={() => pick(call.id)}
              onJump={(index) => {
                const target = list[i + 1];
                if (!target) return;
                pick(target.id);
                setJump({ index, nonce: Date.now() });
              }}
            />
          ))}
        </section>
      </div>
    </div>
  );
}

const fmtMs = (v: number | null) =>
  v == null ? "" : v < 1000 ? `${v}ms` : `${(v / 1000).toFixed(1)}s`;

/** One provider call and what came back. The selected card opens up: the full
 *  answer, its reasoning folded, each tool it asked for, and the results that
 *  answered those calls in the next request — click one to jump there. */
function CallCard({
  call,
  index,
  selected,
  detail,
  next,
  onPick,
  onJump,
}: {
  call: RequestSummary;
  index: number;
  selected: boolean;
  detail: RequestDetail | null;
  next: RequestDetail | null;
  onPick: () => void;
  onJump: (messageIndex: number) => void;
}) {
  const response = detail?.response ?? null;
  const reasoning = reasoningOf(response);
  const content = typeof response?.content === "string" ? response.content : "";
  const asked = toolCallsOf(response);
  const answers = answersIn(response, next);
  return (
    <div
      className="traj-call traj-in"
      aria-current={selected}
      style={{ ["--traj-i" as string]: Math.min(index, 8) }}
    >
      <button type="button" className="traj-call-head" onClick={onPick}>
        <span className="traj-call-r" data-status={call.status}>
          {requestLabel(call)}
        </span>
        <span style={mono}>
          {fmt(call.estTokens)} est
          {call.promptTokens != null
            ? ` · ${fmt(call.promptTokens)} reported`
            : ""}
        </span>
        <span style={{ flex: 1 }} />
        {call.reconstructed ? <span style={mono}>rebuilt</span> : null}
        <span style={mono}>
          {call.status === "pending" ? "in flight" : fmtMs(call.durationMs)}
        </span>
      </button>
      <div className="traj-call-body">
        {call.error ? (
          <div style={{ ...mono, color: "var(--danger)" }}>{call.error}</div>
        ) : null}
        {!selected ? (
          call.toolNames.length ? (
            <div className="traj-call-chips">
              {call.toolNames.map((name, k) => (
                <span key={k} className="traj-call-chip">
                  {name}
                </span>
              ))}
            </div>
          ) : call.responsePreview ? (
            <div className="traj-call-text traj-call-preview">
              {call.responsePreview}
            </div>
          ) : null
        ) : (
          <>
            {reasoning ? (
              <details className="traj-call-reasoning">
                <summary style={mono}>
                  reasoning · {fmt(reasoning.length)} chars
                </summary>
                <pre className="traj-call-text">{reasoning}</pre>
              </details>
            ) : null}
            {content ? (
              <>
                <div className="traj-call-k">Response · assistant</div>
                <pre className="traj-call-text">{content}</pre>
              </>
            ) : null}
            {asked.length ? (
              <>
                <div className="traj-call-k">Tool calls</div>
                {asked.map((c, k) => (
                  <div key={k} className="traj-call-asked">
                    <span className="traj-call-chip">{c.name}</span>
                    <span style={mono}>{c.id || "no id"}</span>
                  </div>
                ))}
              </>
            ) : null}
            {answers.length && next ? (
              <>
                <div className="traj-call-k">
                  Answered in {requestLabel(next)}
                </div>
                {answers.map((m) => (
                  <button
                    type="button"
                    key={m.index}
                    className="traj-call-tool"
                    onClick={() => onJump(m.index)}
                    title="Open this result in the next call"
                  >
                    <span style={mono}>#{m.index}</span>
                    <span>{m.segments[0]?.toolName ?? "tool"}</span>
                    <span style={{ flex: 1 }} />
                    <span style={mono}>{fmt(m.tokens)} tok</span>
                  </button>
                ))}
              </>
            ) : null}
            {!detail ? <div style={mono}>loading…</div> : null}
          </>
        )}
      </div>
    </div>
  );
}
