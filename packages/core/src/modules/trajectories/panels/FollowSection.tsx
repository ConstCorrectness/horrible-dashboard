/**
 * Follow: the agent chat's turns, watched from the inside.
 *
 * Locks onto the chat pane used most recently (or one turn of it, pinned from a
 * reply's trace button) and shows, for the selected turn:
 *
 * - **Context bar**: the round's tokens by where they came from.
 * - **Prompt**: the context as of the selected round, block by block, coloured by
 *   source. A loaded skill wears the skill catalog's colour and a loaded tool group
 *   the tool guides' colour, so injected text is recognisably what offered it.
 *   Blocks new in the selected round are outlined.
 * - **Rounds**: one band per round, with what the round added to or removed from
 *   the context, and the tool calls it made.
 *
 * Observation only: no explanatory copy. The data is three records joined on the
 * chat's turn id (see follow-model.ts), all of which already existed; this section
 * is the join.
 */
import { useContext, useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { PaneInstanceContext } from '../../../agent-context';
import { apiGet } from '../../../api';
import { IconClose, IconExternal, IconMinus, IconPin, IconPinOff } from '../../../glyphs';
import { focusInstance, openPane, revealSection } from '../../../layout/controller';
import { listPanes } from '../../../layout/model';
import { layoutStore } from '../../../layout/store';
import { Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { RollingCounter } from '../../../viz/RollingCounter';
import {
  chatState,
  setFollowTarget,
  setTraceDrawer,
  subscribeChats,
  useFollowTarget,
} from '../../agent/chat-state';
import {
  interpretabilityStore,
  type ContextBlock,
  type RoundSnapshot,
  type TurnSnapshot,
} from '../../interpretability/store';
import {
  getRunByTurn,
  listDatasets,
  updateDataset,
  type TrajectoryDetail,
  type TrajectoryStep,
} from '../api';
import { getTrajectoriesLive, initTrajectoriesLive, subscribeTrajectoriesLive } from '../ws';
import {
  diffChips,
  diffRounds,
  FAMILY_LABEL,
  FAMILY_ORDER,
  familyOf,
  selectTurn,
  stepsByRound,
  tokensByFamily,
  toolGroups,
  turnRows,
  type SourceFamily,
  type TurnRow,
} from './follow-model';
import { StepRow } from './RunDetail';
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
          {turnId ? <TurnView key={turnId} turnId={turnId} /> : null}
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

/** Interpretability's record of a turn: the live ring when it has it, else fetched
 *  (a turn scrubbed back to has usually left the ring). */
function useContextTurn(turnId: string): TurnSnapshot | null {
  const turns = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getSnapshot,
  );
  const live = turns.find((t) => t.turnId === turnId) ?? null;
  const [fetched, setFetched] = useState<TurnSnapshot | null>(null);
  useEffect(() => {
    if (live) return;
    let cancelled = false;
    apiGet<TurnSnapshot>(`/interpretability/turns/${encodeURIComponent(turnId)}`)
      .then((t) => !cancelled && setFetched(t))
      .catch(() => !cancelled && setFetched(null));
    return () => {
      cancelled = true;
    };
  }, [turnId, live]);
  return live ?? fetched;
}

/** The trajectory run for a turn: the live channel while it streams, else fetched.
 *  Null when capture was off for that turn. */
function useRun(turnId: string): { run: TrajectoryDetail | null; missing: boolean } {
  useEffect(() => initTrajectoriesLive(), []);
  const live = useSyncExternalStore(subscribeTrajectoriesLive, getTrajectoriesLive);
  const liveRun = [...live.runs.values()].find((r) => r.run.turn_id === turnId) ?? null;
  const [fetched, setFetched] = useState<TrajectoryDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const sealed = liveRun ? !liveRun.live : null;
  useEffect(() => {
    let cancelled = false;
    getRunByTurn(turnId)
      .then((r) => {
        if (cancelled) return;
        setFetched(r);
        setMissing(false);
      })
      .catch(() => !cancelled && setMissing(true));
    return () => {
      cancelled = true;
    };
    // Re-read when the live copy seals: the stored run has full payloads, the
    // live one has anything over the wire cap replaced by its size.
  }, [turnId, sealed]);
  if (liveRun?.live) {
    const run = {
      ...(fetched ?? {}),
      ...liveRun.run,
      step_list: liveRun.steps,
      labels: fetched?.labels ?? [],
      harness_detail: fetched?.harness_detail ?? null,
    } as TrajectoryDetail;
    return { run, missing: false };
  }
  return { run: fetched, missing: missing && !liveRun };
}

function TurnView({ turnId }: { turnId: string }) {
  const turn = useContextTurn(turnId);
  const { run, missing } = useRun(turnId);
  const rounds = turn?.rounds ?? [];
  const [picked, setPicked] = useState<number | null>(null);
  const roundIdx = picked ?? Math.max(0, rounds.length - 1);
  const round = rounds[roundIdx];
  const steps = useMemo(() => stepsByRound(run?.step_list ?? []), [run?.step_list]);

  if (!turn || !round) {
    return (
      <div className="traj-follow-main">
        <EmptyState title="Waiting for round 0">
          {turn?.kind === 'peer' ? 'Ran on another node.' : null}
        </EmptyState>
      </div>
    );
  }
  const diff = diffRounds(rounds[roundIdx - 1], round);
  const added = new Set(diff.added);

  return (
    <div className="traj-follow-main">
      <ContextBar round={round} />
      <div className="traj-follow-cols">
        <section className="traj-follow-prompt" aria-label="Context">
          <h3 className="traj-follow-h">
            Context <span className="traj-follow-h-meta">R{round.round}</span>
          </h3>
          {round.blocks.map((b, i) => (
            <Segment
              key={`${b.hash ?? b.kind}-${i}`}
              block={b}
              isNew={roundIdx > 0 && added.has(i)}
            />
          ))}
          <ToolsOffered round={round} />
        </section>
        <section className="traj-follow-rounds" aria-label="Rounds">
          <h3 className="traj-follow-h">
            Rounds
            <span className="traj-follow-h-meta" title={turn.model || undefined}>
              {rounds.length}
              {turn.model ? ` · ${turn.model}` : ''}
            </span>
            {missing ? <CaptureOff /> : null}
          </h3>
          {rounds.map((r, i) => (
            <RoundBand
              key={r.round}
              round={r}
              prev={rounds[i - 1]}
              index={i}
              selected={i === roundIdx}
              onPick={() => setPicked(i === rounds.length - 1 ? null : i)}
              runId={run?.id ?? ''}
              steps={steps.get(r.round) ?? []}
            />
          ))}
        </section>
      </div>
    </div>
  );
}

function ContextBar({ round }: { round: RoundSnapshot }) {
  const by = tokensByFamily(round);
  const total = [...by.values()].reduce((n, v) => n + v, 0) || 1;
  const present = FAMILY_ORDER.filter((f) => (by.get(f) ?? 0) > 0);
  return (
    <div className="traj-follow-bar">
      <div className="traj-follow-bar-track" role="img" aria-label="Context tokens by source">
        {present.map((f) => (
          <span
            key={f}
            className="traj-follow-bar-seg"
            data-family={f}
            style={{ width: `${((by.get(f) ?? 0) / total) * 100}%` }}
          />
        ))}
      </div>
      <div className="traj-follow-legend">
        {present.map((f) => (
          <span key={f} className="traj-follow-legend-item" data-family={f}>
            <span className="traj-follow-swatch" />
            {FAMILY_LABEL[f]}
            <RollingCounter value={by.get(f) ?? 0} format={fmt} className="traj-follow-num" />
          </span>
        ))}
        <span className="traj-follow-legend-total">
          <RollingCounter value={round.totalTokens} format={fmt} className="traj-follow-num" /> tok
        </span>
      </div>
    </div>
  );
}

function Segment({ block, isNew }: { block: ContextBlock; isNew: boolean }) {
  const family: SourceFamily = familyOf(block.kind);
  // The harness's own text starts open; conversation and results start folded.
  const [open, setOpen] = useState(
    family === 'system' || family === 'skills' || family === 'tools' || isNew,
  );
  return (
    <div className="traj-follow-seg" data-family={family} data-new={isNew || undefined}>
      <button
        type="button"
        className="traj-follow-seg-head"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="traj-follow-seg-label">{block.label}</span>
        {block.toolName ? <span style={mono}>{block.toolName}</span> : null}
        <span style={{ flex: 1 }} />
        {isNew ? <span className="traj-follow-new">new</span> : null}
        {block.clipped ? <span style={mono}>clipped</span> : null}
        <span style={mono}>{fmt(block.tokens)}</span>
      </button>
      {open ? (
        <pre className="traj-follow-seg-body">
          {block.content}
          {block.clipped ? `\n… ${block.fullChars - block.content.length} more chars` : ''}
        </pre>
      ) : null}
    </div>
  );
}

function ToolsOffered({ round }: { round: RoundSnapshot }) {
  const [open, setOpen] = useState(false);
  if (!round.tools.length) return null;
  const groups = toolGroups(round.tools);
  return (
    <div className="traj-follow-seg" data-family="tools">
      <button
        type="button"
        className="traj-follow-seg-head"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="traj-follow-seg-label">Tool schemas</span>
        <span style={mono}>
          {round.tools.length}
          {round.toolsTruncated ? ` of ${round.toolsSelected}` : ''} · {groups.length} groups
        </span>
        <span style={{ flex: 1 }} />
        <span style={mono}>{fmt(round.toolTokens)}</span>
      </button>
      {open ? (
        <div className="traj-follow-tools">
          {groups.map((g) => (
            <div key={g.group} className="traj-follow-tool-group">
              <div className="traj-follow-tool-group-head">
                <span>{g.group}</span>
                <span style={mono}>{fmt(g.tokens)}</span>
              </div>
              {g.tools.map((t) => (
                <div key={t.name} className="traj-follow-tool">
                  <span>{t.name}</span>
                  <span style={mono}>{fmt(t.tokens)}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function RoundBand({
  round,
  prev,
  index,
  selected,
  onPick,
  runId,
  steps,
}: {
  round: RoundSnapshot;
  prev: RoundSnapshot | undefined;
  index: number;
  selected: boolean;
  onPick: () => void;
  runId: string;
  steps: TrajectoryStep[];
}) {
  const chips = diffChips(round, diffRounds(prev, round), !prev);
  const delta = prev ? round.totalTokens - prev.totalTokens : round.totalTokens;
  return (
    <div
      className="traj-follow-band traj-in"
      aria-current={selected}
      style={{ ['--traj-i' as string]: Math.min(index, 8) }}
    >
      <button type="button" className="traj-follow-band-head" onClick={onPick}>
        <span className="traj-follow-band-r">R{round.round}</span>
        <span style={mono}>
          {fmt(round.totalTokens)} tok{prev ? ` · ${delta >= 0 ? '+' : ''}${fmt(delta)}` : ''}
        </span>
        <span style={{ flex: 1 }} />
        <span style={mono}>{round.tools.length} tools</span>
      </button>
      {chips.length ? (
        <div className="traj-follow-chips">
          {chips.map((c, i) => (
            <span key={i} className="traj-follow-chip" data-family={c.family}>
              <b>{c.sign}</b> {c.label}
              {c.tokens ? <span style={mono}> {fmt(c.tokens)}</span> : null}
            </span>
          ))}
        </div>
      ) : null}
      {steps.map((step, i) => (
        <StepRow key={step.seq} runId={runId} step={step} index={i} selected={false} />
      ))}
    </div>
  );
}

/** Steps need trajectory capture; context doesn't. Say which is missing, and fix it
 *  in place. */
function CaptureOff() {
  const [busy, setBusy] = useState(false);
  const turnOn = async () => {
    setBusy(true);
    try {
      const datasets = await listDatasets();
      const target = datasets.find((d) => d.id === 'dashboard-agent') ?? datasets[0];
      if (target) await updateDataset(target.id, { capture: true });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Chip kind="warn">
      <button
        type="button"
        className="traj-follow-capture"
        disabled={busy}
        onClick={() => void turnOn()}
      >
        capture off · turn on
      </button>
    </Chip>
  );
}
