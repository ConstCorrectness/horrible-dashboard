/**
 * Agentpedia: one agent turn, steppable.
 *
 * Three sections in one pane, because they are three views of one object — the
 * turns, the configuration that produced them, and the counterfactuals branched off
 * them. `r` / `h` / `f` switch between them (declared as section keys, so the host's
 * tab strip and the keyboard move the same value).
 *
 * The Runs section is the point. A round is shown in four columns:
 *
 * - **Shown** — the context blocks and tool schemas, rendered by the same
 *   components the interpretability pane uses (`core/ContextBlocks`), so the two
 *   panes can never disagree about what a round contained.
 * - **Wire** — what actually left the machine, matched to this round by the
 *   `turn_id`/`round` stamp, plus what the provider seam does to the message list
 *   on the way out. The context pane shows the system tier pre-flatten; the
 *   provider receives one leading system message. Both are true, and a reader
 *   comparing them without being told would conclude one is lying.
 * - **Did** — the trajectory steps for the round. Empty is ambiguous unless you say
 *   why, so an empty column distinguishes "capture is off" from "this round called
 *   nothing".
 * - **Cost** — tokens, and the share of the real context window when it is known.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { BlockRow, CompositionBar, fmtTokens, TokenizerBadge, ToolList } from '../../ContextBlocks';
import { IconAlert, IconCheck, IconChevron, IconClock, IconRetry } from '../../glyphs';
import { usePaneSection } from '../../layout/use-sections';
import { bindStepper } from './actions';
import { ForksSection, type ForkTarget } from './ForksSection';
import {
  getTurn,
  harnessTools,
  listHarnesses,
  listTurns,
  type DidStep,
  type Harness,
  type RoundView,
  type ToolStat,
  type TurnIndexEntry,
  type TurnView,
  type WireEvent,
} from './api';
import * as S from './styles';

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—';
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`;
}

/**
 * A number that rolls up to its value.
 *
 * Seeded at the **final** value with a timeout that snaps to it, not animated up
 * from zero: `requestAnimationFrame` does not fire in a backgrounded tab, and a
 * tile reading 0 when it means 8,413 is worse than no animation at all.
 */
function useRolling(value: number): number {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = from.current;
    from.current = value;
    if (start === value) return;
    const began = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - began) / 380);
      setShown(Math.round(start + (value - start) * (1 - Math.pow(1 - t, 3))));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    const snap = setTimeout(() => setShown(value), 500);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(snap);
    };
  }, [value]);
  return shown;
}

// ── Runs: the timeline ───────────────────────────────────────────────────────

function TurnRow({
  entry,
  active,
  onOpen,
  index,
}: {
  entry: TurnIndexEntry;
  active: boolean;
  onOpen: () => void;
  index: number;
}) {
  return (
    <button
      onClick={onOpen}
      style={{
        ...S.card(active),
        ...S.stagger(index),
        width: '100%',
        textAlign: 'left',
        display: 'grid',
        gap: 6,
        marginBottom: 6,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={S.heading}>{entry.agent_name || entry.agent_id}</span>
        {entry.kind === 'peer' && <span style={S.statusPill('idle')}>peer</span>}
        {entry.run && (
          <span style={S.statusPill(entry.run.outcome === 'success' ? 'ok' : 'idle')}>
            {entry.run.outcome ?? entry.run.status}
          </span>
        )}
        <span style={{ ...S.mono, marginLeft: 'auto' }}>{fmtTime(entry.started_at)}</span>
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <span style={S.mono}>{entry.turn_id}</span>
        <span style={S.mono}>· {entry.model}</span>
      </div>
      <div style={{ display: 'flex', gap: 10 }}>
        <span style={S.mono}>
          {entry.rounds} round{entry.rounds === 1 ? '' : 's'}
        </span>
        <span style={S.mono}>{fmtTokens(entry.total_tokens)} tok</span>
      </div>
    </button>
  );
}

// ── Runs: the four columns ───────────────────────────────────────────────────

function ShownColumn({ round }: { round: RoundView }) {
  return (
    <div style={S.column}>
      <div style={S.columnHead}>
        <span>Shown</span>
        <span style={S.mono}>{fmtTokens(round.cost.total_tokens)} tok</span>
      </div>
      <div style={{ padding: 10, display: 'grid', gap: 8 }}>
        <CompositionBar round={round.shown} />
        {round.shown.blocks.map((block, i) => (
          <BlockRow key={`${block.kind}-${i}`} block={block} />
        ))}
        <ToolList round={round.shown} />
      </div>
    </div>
  );
}

function WireRow({ event }: { event: WireEvent }) {
  const [open, setOpen] = useState(false);
  const failed = event.error != null || (event.status != null && event.status >= 400);
  return (
    <div style={{ ...S.card(false), cursor: 'default', marginBottom: 6 }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          background: 'none',
          border: 0,
          padding: 0,
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          cursor: 'pointer',
          color: 'inherit',
        }}
      >
        <span style={S.statusPill(failed ? 'bad' : 'ok')}>
          {failed ? <IconAlert width={11} height={11} /> : <IconCheck width={11} height={11} />}
          {event.status ?? event.error ?? '—'}
        </span>
        <span style={{ ...S.mono, color: 'var(--text-primary)' }}>{event.method}</span>
        <span
          style={{
            ...S.mono,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {event.target}
        </span>
        <span style={{ ...S.mono, marginLeft: 'auto' }}>{fmtMs(event.duration_ms)}</span>
        <IconChevron
          width={12}
          height={12}
          style={{ transform: open ? 'rotate(90deg)' : 'none', flexShrink: 0 }}
        />
      </button>
      {open && (
        <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
          <div style={S.heading}>Request</div>
          <pre style={S.code}>{event.request_body ?? '(body not captured)'}</pre>
          <div style={S.heading}>Response</div>
          <pre style={S.code}>{event.response_body ?? '(streamed or not captured)'}</pre>
        </div>
      )}
    </div>
  );
}

/** Why the wire column is empty, in the words of the reason it is empty. */
function WireEmpty({ status }: { status: TurnView['wire_status'] }) {
  if (status === 'aged_out') {
    return (
      <div style={S.empty}>
        The telemetry ring has moved past this turn.
        <br />
        It holds the last 500 events in memory and is not written to disk, so the requests this turn
        made are gone even though the turn itself is stored.
      </div>
    );
  }
  return (
    <div style={S.empty}>
      No wire recorded for this turn.
      <br />
      Turns captured before requests carried a <code>turn_id</code> stamp have no way to be matched
      to one.
    </div>
  );
}

function WireColumn({ round, status }: { round: RoundView; status: TurnView['wire_status'] }) {
  const { flatten } = round;
  const flattened = flatten.messages_in !== flatten.messages_out;
  return (
    <div style={S.column}>
      <div style={S.columnHead}>
        <span>Wire</span>
        <span style={S.mono}>{round.wire.length} req</span>
      </div>
      <div style={{ padding: 10 }}>
        {flattened && (
          <div style={{ ...S.card(false), cursor: 'default', marginBottom: 8 }}>
            <div style={S.heading}>Flattened at the provider seam</div>
            <div style={{ ...S.mono, marginTop: 6, lineHeight: 1.6 }}>
              {flatten.messages_in} messages → {flatten.messages_out}. The Shown column is the
              pre-flatten split the recorder labels by position; the provider received one leading
              system message, because strict Jinja templates reject a second one.
            </div>
            {flatten.merged.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 8 }}>
                {flatten.merged.map((label) => (
                  <span key={label} style={S.pill}>
                    {label}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
        {round.wire.length === 0 ? (
          <WireEmpty status={status} />
        ) : (
          round.wire.map((event) => <WireRow key={event.id} event={event} />)
        )}
      </div>
    </div>
  );
}

function DidRow({ step }: { step: DidStep }) {
  const tone = step.gated ? 'warn' : step.ok === false ? 'bad' : 'ok';
  return (
    <div style={{ ...S.card(false), cursor: 'default', marginBottom: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={S.statusPill(tone)}>{step.gated ? 'gated' : step.kind}</span>
        <span style={{ ...S.mono, color: 'var(--text-primary)' }}>{step.name ?? step.kind}</span>
        <span style={{ ...S.mono, marginLeft: 'auto' }}>{fmtMs(step.duration_ms)}</span>
      </div>
      {step.error && <pre style={{ ...S.code, marginTop: 6 }}>{step.error}</pre>}
      {step.content && <pre style={{ ...S.code, marginTop: 6 }}>{step.content}</pre>}
      {step.args != null && (
        <pre style={{ ...S.code, marginTop: 6 }}>{JSON.stringify(step.args, null, 2)}</pre>
      )}
    </div>
  );
}

function DidColumn({ round, captureOn }: { round: RoundView; captureOn: boolean }) {
  return (
    <div style={S.column}>
      <div style={S.columnHead}>
        <span>Did</span>
        <span style={S.mono}>{round.did.length} steps</span>
      </div>
      <div style={{ padding: 10 }}>
        {round.did.length === 0 ? (
          <div style={S.empty}>
            {captureOn
              ? 'This round called nothing.'
              : 'Trajectory capture is off, so what the agent did was never recorded. Turn it on for a dataset in the Trajectories pane.'}
          </div>
        ) : (
          round.did.map((step) => <DidRow key={step.seq} step={step} />)
        )}
      </div>
    </div>
  );
}

function CostColumn({ round }: { round: RoundView }) {
  const total = useRolling(round.cost.total_tokens);
  const { window: ctx, window_pct: pct } = round.cost;
  return (
    <div style={{ ...S.column, borderRight: 0 }}>
      <div style={S.columnHead}>
        <span>Cost</span>
      </div>
      <div style={{ padding: 10, display: 'grid', gap: 10 }}>
        <div>
          <div style={{ fontSize: 26, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
            {total.toLocaleString()}
          </div>
          <div style={S.mono}>tokens in this round</div>
        </div>
        <div style={{ display: 'grid', gap: 4 }}>
          <div style={S.mono}>messages · {fmtTokens(round.cost.message_tokens)}</div>
          <div style={S.mono}>tools · {fmtTokens(round.cost.tool_tokens)}</div>
        </div>
        {ctx ? (
          <div style={{ display: 'grid', gap: 4 }}>
            <div style={S.heading}>Window</div>
            <div
              style={{
                height: 6,
                borderRadius: 3,
                background: 'var(--bg-tertiary)',
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  width: `${Math.min(100, pct ?? 0)}%`,
                  height: '100%',
                  background: (pct ?? 0) > 85 ? 'var(--warning)' : 'var(--accent)',
                }}
              />
            </div>
            <div style={S.mono}>
              {(pct ?? 0).toFixed(1)}% of {ctx.toLocaleString()}
            </div>
          </div>
        ) : (
          <div style={S.mono}>Context window unknown for this provider.</div>
        )}
      </div>
    </div>
  );
}

// ── Runs: the stepper ────────────────────────────────────────────────────────

function Stepper({
  turn,
  captureOn,
  onBack,
  onFork,
}: {
  turn: TurnView;
  captureOn: boolean;
  onBack: () => void;
  onFork: (round: number) => void;
}) {
  const [index, setIndex] = useState(0);
  const round = turn.rounds[Math.min(index, turn.rounds.length - 1)];

  // Publish the scrubbing verbs for the ←/→ bindings while this stepper is mounted.
  // Bound to `turn.rounds.length` rather than to a ref so the clamp cannot outlive
  // the turn it was computed for.
  useEffect(() => {
    bindStepper({
      prevRound: () => setIndex((i) => Math.max(0, i - 1)),
      nextRound: () => setIndex((i) => Math.min(turn.rounds.length - 1, i + 1)),
    });
    return () => bindStepper(null);
  }, [turn.rounds.length]);

  useEffect(() => setIndex(0), [turn.turn_id]);

  if (!round) {
    return (
      <div style={S.empty}>
        This turn has no rounds.
        {turn.kind === 'peer' && (
          <>
            <br />
            It reached another node through <code>agent.ask_peer</code>: the peer assembled its own
            context on its own machine, and we have no visibility into it.
          </>
        )}
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
      <div style={S.bar}>
        <button style={S.ghostButton} onClick={onBack}>
          <IconChevron width={12} height={12} style={{ transform: 'rotate(180deg)' }} />
          Timeline
        </button>
        <span style={S.heading}>{turn.agent_name || turn.agent_id}</span>
        <span style={S.mono}>{turn.model}</span>
        <TokenizerBadge
          exact={turn.exact}
          repo={turn.tokenizer_repo}
          source={turn.tokenizer_source}
        />
        {turn.run && (
          <span style={S.statusPill(turn.run.outcome === 'success' ? 'ok' : 'idle')}>
            {turn.run.outcome ?? turn.run.status}
          </span>
        )}
        <span style={{ ...S.mono, marginLeft: 'auto' }}>
          <IconClock width={11} height={11} /> {fmtTime(turn.started_at)}
        </span>
      </div>

      <div style={{ ...S.bar, gap: 6, flexWrap: 'wrap' }}>
        <button
          style={S.ghostButton}
          onClick={() => setIndex((i) => Math.max(0, i - 1))}
          disabled={index === 0}
          aria-label="Previous round"
        >
          ←
        </button>
        {turn.rounds.map((r, i) => (
          <button
            key={r.round}
            onClick={() => setIndex(i)}
            style={{
              ...S.ghostButton,
              padding: '0 12px',
              borderColor: i === index ? 'var(--accent)' : 'var(--border)',
              color: i === index ? 'var(--text-primary)' : 'var(--text-secondary)',
            }}
          >
            R{r.round}
          </button>
        ))}
        <button
          style={S.ghostButton}
          onClick={() => setIndex((i) => Math.min(turn.rounds.length - 1, i + 1))}
          disabled={index >= turn.rounds.length - 1}
          aria-label="Next round"
        >
          →
        </button>
        <span style={{ ...S.mono, marginLeft: 'auto' }}>
          round {index + 1} of {turn.rounds.length}
        </span>
        {/* The verb the whole module builds toward. It carries the round, not just
            the turn: a fork branches at the context the model was holding *here*,
            and forking from round 0 when you are reading round 3 would replay a
            different question. */}
        <button style={S.ghostButton} onClick={() => onFork(round.round)}>
          ⑂ Fork this round
        </button>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0,1.2fr) minmax(0,1fr) minmax(0,1fr) 180px',
          minHeight: 0,
          flex: 1,
        }}
      >
        <ShownColumn round={round} />
        <WireColumn round={round} status={turn.wire_status} />
        <DidColumn round={round} captureOn={captureOn} />
        <CostColumn round={round} />
      </div>
    </div>
  );
}

function RunsSection({ onFork }: { onFork: (turnId: string, round: number) => void }) {
  const [index, setIndex] = useState<TurnIndexEntry[]>([]);
  const [captureOn, setCaptureOn] = useState(false);
  const [open, setOpen] = useState<TurnView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    setBusy(true);
    listTurns()
      .then((res) => {
        setIndex(res.turns);
        setCaptureOn(res.capture_on);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  }, []);

  useEffect(refresh, [refresh]);

  const openTurn = useCallback((id: string) => {
    getTurn(id)
      .then(setOpen)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (open) {
    return (
      <Stepper
        turn={open}
        captureOn={captureOn}
        onBack={() => setOpen(null)}
        onFork={(round) => onFork(open.turn_id, round)}
      />
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
      <div style={S.bar}>
        <span style={S.heading}>Turns</span>
        <span style={S.mono}>{index.length}</span>
        {!captureOn && (
          <span style={S.statusPill('idle')} title="What the agent did is not being recorded">
            trajectory capture off
          </span>
        )}
        <button style={{ ...S.ghostButton, marginLeft: 'auto' }} onClick={refresh} disabled={busy}>
          <IconRetry width={12} height={12} />
          Refresh
        </button>
      </div>
      <div style={{ overflow: 'auto', padding: 10, minHeight: 0, flex: 1 }}>
        {error && <div style={{ ...S.empty, color: 'var(--error)' }}>{error}</div>}
        {!error && index.length === 0 && (
          <div style={S.empty}>
            No turns recorded yet.
            <br />
            Ask the agent something and it will appear here.
          </div>
        )}
        {index.map((entry, i) => (
          <TurnRow
            key={entry.turn_id}
            entry={entry}
            index={i}
            active={false}
            onOpen={() => openTurn(entry.turn_id)}
          />
        ))}
      </div>
    </div>
  );
}

// ── Harness ──────────────────────────────────────────────────────────────────

function HarnessDetail({ harness }: { harness: Harness }) {
  const [tools, setTools] = useState<ToolStat[]>([]);
  useEffect(() => {
    harnessTools(harness.fingerprint)
      .then((r) => setTools(r.tools))
      .catch(() => setTools([]));
  }, [harness.fingerprint]);

  return (
    <div style={{ padding: 10, display: 'grid', gap: 10, overflow: 'auto' }}>
      <div>
        <div style={S.heading}>System prompt</div>
        <pre style={{ ...S.code, marginTop: 6 }}>{harness.system_prompt || '(empty)'}</pre>
      </div>
      <div>
        <div style={S.heading}>Tools offered · {harness.tool_names.length}</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
          {harness.tool_names.map((name) => (
            <span key={name} style={{ ...S.pill, fontFamily: 'var(--font-mono)' }}>
              {name}
            </span>
          ))}
        </div>
      </div>
      <div>
        <div style={S.heading}>Calls</div>
        {tools.length === 0 ? (
          <div style={S.empty}>No calls recorded under this harness.</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 6 }}>
            <thead>
              <tr>
                {['tool', 'calls', 'failed', 'gated', 'avg'].map((h) => (
                  <th key={h} style={{ ...S.heading, textAlign: 'left', padding: '4px 6px' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tools.map((t) => (
                <tr key={t.name} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ ...S.mono, padding: '4px 6px', color: 'var(--text-primary)' }}>
                    {t.name}
                  </td>
                  <td style={{ ...S.mono, padding: '4px 6px' }}>{t.calls}</td>
                  <td style={{ ...S.mono, padding: '4px 6px' }}>{t.failures}</td>
                  <td style={{ ...S.mono, padding: '4px 6px' }}>{t.gated}</td>
                  <td style={{ ...S.mono, padding: '4px 6px' }}>{fmtMs(t.avg_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function HarnessSection() {
  const [harnesses, setHarnesses] = useState<Harness[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    listHarnesses()
      .then((r) => setHarnesses(r.harnesses))
      .catch(() => setHarnesses([]));
  }, []);

  const current = useMemo(
    () => harnesses.find((h) => h.fingerprint === selected) ?? null,
    [harnesses, selected],
  );

  if (harnesses.length === 0) {
    return (
      <div style={S.empty}>
        No harnesses recorded.
        <br />A harness is fingerprinted when a run is captured, so this fills in once trajectory
        capture is on for a dataset.
      </div>
    );
  }

  return (
    <div
      style={{ display: 'grid', gridTemplateColumns: '280px minmax(0,1fr)', minHeight: 0, flex: 1 }}
    >
      <div style={{ ...S.column, padding: 10 }}>
        {harnesses.map((h, i) => (
          <button
            key={h.fingerprint}
            onClick={() => setSelected(h.fingerprint)}
            style={{
              ...S.card(h.fingerprint === selected),
              ...S.stagger(i),
              width: '100%',
              textAlign: 'left',
              display: 'grid',
              gap: 4,
              marginBottom: 6,
            }}
          >
            <span style={S.heading}>{h.label || h.agent_id}</span>
            <span style={S.mono}>{h.fingerprint.slice(0, 12)}</span>
            <span style={S.mono}>
              {h.run_count} run{h.run_count === 1 ? '' : 's'} · {h.tool_names.length} tools
            </span>
          </button>
        ))}
      </div>
      {current ? (
        <HarnessDetail harness={current} />
      ) : (
        <div style={S.empty}>Pick a harness to see its prompt, its tools and how they fared.</div>
      )}
    </div>
  );
}

// ── The pane ─────────────────────────────────────────────────────────────────

export function AgentpediaHub() {
  const { section, setSection } = usePaneSection();
  // Which round a fork would branch at, handed from Runs to Forks. Held here
  // rather than in a module singleton because the sections are siblings and this
  // is the one value they share — and `setSection` is what moves the tab strip,
  // the keyboard and the pane's own buttons together.
  const [forkTarget, setForkTarget] = useState<ForkTarget | null>(null);

  const startFork = useCallback(
    (turnId: string, round: number) => {
      setForkTarget({ turnId, round });
      setSection('forks');
    },
    [setSection],
  );

  return (
    <div style={S.pane}>
      {section === 'harness' ? (
        <HarnessSection />
      ) : section === 'forks' ? (
        <ForksSection target={forkTarget} onClearTarget={() => setForkTarget(null)} />
      ) : (
        <RunsSection onFork={startFork} />
      )}
    </div>
  );
}
