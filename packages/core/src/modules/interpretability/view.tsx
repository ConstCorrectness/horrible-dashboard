import { useMemo, useState, useSyncExternalStore } from 'react';

import {
  BlockRow,
  CompositionBar,
  fmtTokens,
  TokenizerBadge,
  ToolList,
} from '../../ContextBlocks';
import {
  agentsIn,
  buildTurnTree,
  interpretabilityStore,
  type TurnSnapshot,
} from './store';

function useTurns(): TurnSnapshot[] {
  return useSyncExternalStore(interpretabilityStore.subscribe, interpretabilityStore.getSnapshot);
}

function useModelInfo() {
  return useSyncExternalStore(interpretabilityStore.subscribe, interpretabilityStore.getModelInfo);
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString();
}

/**
 * The window the prompt is measured against: what the model actually has, falling
 * back to what we requested. Reported separately in the header because the two
 * disagreeing is itself the finding — asking for more `num_ctx` than the model has
 * gets you the model's real limit, silently.
 */
function effectiveWindow(turn: TurnSnapshot, modelCtx: number | null): number | null {
  return turn.modelContextLength ?? modelCtx ?? turn.requestedNumCtx;
}

/**
 * Who ran this turn and what it was allowed to touch.
 *
 * Scope matters as much as identity: a specialist can only load tools from its
 * declared groups, so "the agent ignored my request" is often really "that agent
 * was never given that capability". `toolGroups: null` means unrestricted, which is
 * a different statement from an empty list and is rendered differently.
 */
function AgentIdentity({ turn }: { turn: TurnSnapshot }) {
  const delegated = turn.parentTurnId !== null;
  return (
    <div className="interp-agentbar">
      <span className="interp-chip interp-kind-agent">{turn.agentName || turn.agentId}</span>
      {delegated && (
        <span className="interp-dim" title={`Delegated by turn ${turn.parentTurnId}`}>
          ↳ delegated
        </span>
      )}
      <span className="interp-dim">
        {turn.toolGroups === null ? (
          'unrestricted scope'
        ) : (
          <>scope: {turn.toolGroups.length ? turn.toolGroups.join(', ') : 'none'}</>
        )}
      </span>
      {turn.permissionMode && <span className="interp-dim">mode: {turn.permissionMode}</span>}
    </div>
  );
}

/**
 * An `agent.ask_peer` handoff. There is deliberately nothing to inspect here: the
 * peer's agent built its context on its own machine, and this node never sees it.
 * Saying that plainly beats an empty pane that looks like a bug.
 */
function PeerTurn({ turn }: { turn: TurnSnapshot }) {
  return (
    <div className="interp-header">
      <div className="interp-header-row">
        <span className="interp-chip interp-kind-peer">peer</span>
        <span className="interp-model">{turn.peerId}</span>
        <span className="interp-dim">asked via agent.ask_peer</span>
      </div>
      <div className="interp-warn interp-peer-note">
        This turn ran on another user&apos;s node. Its context was assembled there and is visible
        only in that node&apos;s own interpretability pane — nothing about it crosses the fabric.
      </div>
      {turn.sentPrompt && (
        <div className="interp-block">
          <div className="interp-block-head">
            <span className="interp-chip interp-kind-user">Prompt sent</span>
          </div>
          <pre className="interp-content">{turn.sentPrompt}</pre>
        </div>
      )}
    </div>
  );
}

/** Header: what model, what window, and whether the numbers can be trusted as exact. */
function TurnHeader({ turn }: { turn: TurnSnapshot }) {
  const modelInfo = useModelInfo();
  const round = turn.rounds[turn.rounds.length - 1];
  const used = round?.totalTokens ?? 0;
  const window = effectiveWindow(turn, modelInfo?.contextLength ?? null);
  const pct = window ? Math.min(100, (used / window) * 100) : null;
  const over = window != null && used > window;

  return (
    <div className="interp-header">
      <AgentIdentity turn={turn} />
      <div className="interp-header-row">
        <span className="interp-model">{turn.model || modelInfo?.model || 'unknown model'}</span>
        {modelInfo?.parameters && <span className="interp-dim">{modelInfo.parameters}</span>}
        <span className="interp-dim">{turn.provider || modelInfo?.provider}</span>
        <TokenizerBadge
          exact={turn.exact}
          repo={turn.tokenizerRepo}
          source={turn.tokenizerSource}
        />
      </div>
      <div className="interp-budget">
        <div className="interp-budget-bar">
          <div
            className={`interp-budget-fill${over ? ' interp-budget-over' : ''}`}
            style={{ width: `${pct ?? 0}%` }}
          />
        </div>
        <span className="interp-budget-label">
          {fmtTokens(used)} / {window ? fmtTokens(window) : '?'} tok
          {pct != null && ` (${pct.toFixed(0)}%)`}
        </span>
        {/* Carried up from what used to be the separate budget widget: the two
            things worth knowing without scrolling are how full the window is and
            whether tools were silently dropped before the prompt was sent. The
            full explanation still sits with the tool list below. */}
        {round?.toolsTruncated && (
          <span
            className="interp-warn-chip"
            title={`${round.toolsSelected - round.toolBudget} tools were dropped before this prompt was sent.`}
          >
            tools dropped
          </span>
        )}
      </div>
      {turn.requestedNumCtx != null &&
        turn.modelContextLength != null &&
        turn.requestedNumCtx > turn.modelContextLength && (
          <div className="interp-warn">
            ⚠ Requested num_ctx ({turn.requestedNumCtx}) exceeds the model&apos;s real context
            length ({turn.modelContextLength}) — the extra is not honoured.
          </div>
        )}
      <div className="interp-params interp-dim">
        temp {turn.temperature ?? '—'} · top_p {turn.topP ?? '—'} · max_tokens{' '}
        {turn.maxTokens ?? '—'} · num_ctx {turn.requestedNumCtx ?? 'default'}
      </div>
    </div>
  );
}

export function InterpretabilityPanel() {
  const turns = useTurns();
  const [selectedTurn, setSelectedTurn] = useState<string | null>(null);
  const [selectedRound, setSelectedRound] = useState<number | null>(null);
  const [agentFilter, setAgentFilter] = useState<string>('');

  const agents = useMemo(() => agentsIn(turns), [turns]);
  const rows = useMemo(() => {
    const tree = buildTurnTree(turns);
    if (!agentFilter) return tree;
    // Filtering flattens: with the siblings hidden, the indent would imply a
    // parent that isn't on screen.
    return tree.filter((r) => r.turn.agentId === agentFilter).map((r) => ({ ...r, depth: 0 }));
  }, [turns, agentFilter]);

  const visible = rows.map((r) => r.turn);
  const turn = visible.find((t) => t.turnId === selectedTurn) ?? visible[0];
  const round =
    turn?.rounds.find((r) => r.round === selectedRound) ?? turn?.rounds[turn.rounds.length - 1];

  if (!turn) {
    return (
      <div className="interp-empty">
        <p>No agent turns captured yet.</p>
        <p className="interp-dim">
          Ask the agent something — this pane shows the exact context each round was given: the
          system prompt, tool guides, replayed history, your focused buffer, and the tool schemas,
          with real token costs.
        </p>
      </div>
    );
  }

  return (
    <div className="interp-panel">
      <div className="interp-turnbar">
        <select
          value={turn.turnId}
          onChange={(e) => {
            setSelectedTurn(e.target.value);
            setSelectedRound(null);
          }}
        >
          {rows.map(({ turn: t, depth }) => (
            <option key={t.turnId} value={t.turnId}>
              {/* U+2514 draws the handoff: a delegated turn reads as belonging to
                  the turn above it, which a flat list can't convey. */}
              {depth > 0 ? `${'  '.repeat(depth)}└ ` : ''}
              {fmtTime(t.startedAt)} · {t.agentName || t.agentId}
              {t.kind === 'peer' ? ' · peer (off-node)' : ` · ${t.rounds.length}r`}
            </option>
          ))}
        </select>
        {agents.length > 1 && (
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            title="Filter captured turns by agent"
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        )}
        <button onClick={() => void interpretabilityStore.refresh()}>Refresh</button>
        <button onClick={() => void interpretabilityStore.clear()}>Clear</button>
      </div>

      {turn.kind === 'peer' ? <PeerTurn turn={turn} /> : <TurnHeader turn={turn} />}

      {turn.kind !== 'peer' && turn.rounds.length > 1 && (
        <div className="interp-rounds">
          {turn.rounds.map((r) => (
            <button
              key={r.round}
              className={`interp-round-tab${r.round === round?.round ? ' interp-round-active' : ''}`}
              onClick={() => setSelectedRound(r.round)}
              title={`${fmtTokens(r.totalTokens)} tokens`}
            >
              Round {r.round + 1}
            </button>
          ))}
        </div>
      )}

      {round && (
        <div className="interp-body">
          <CompositionBar round={round} />
          <div className="interp-subhead">
            Messages: <b>{round.blocks.length}</b> · {fmtTokens(round.messageTokens)} tok
          </div>
          {round.blocks.map((b, i) => (
            <BlockRow key={`${b.kind}-${i}`} block={b} />
          ))}
          <ToolList round={round} />
        </div>
      )}
    </div>
  );
}
