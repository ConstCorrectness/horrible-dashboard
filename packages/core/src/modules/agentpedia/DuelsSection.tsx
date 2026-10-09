/**
 * Duels: two configurations answer the same recorded round, side by side, judged
 * blind; enough votes rank them on your own tasks.
 *
 * Each side is an ordinary fork (tools simulated), run at once by the backend,
 * which also decides at random which contestant is shown as A — so the answers
 * here carry no names until the vote. After it, the names flip in and the rating
 * change is shown: that moment is the point of the section.
 *
 * The leaderboard is Elo replayed from every vote (backend `duel.leaderboard`), so
 * changing a vote or forgetting a duel leaves nothing behind.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { IconRetry } from '../../glyphs';
import {
  availableModels,
  deleteDuel,
  getLeaderboard,
  listDuels,
  runDuel,
  voteDuel,
  type Duel,
  type DuelSide,
  type DuelVote,
  type ForkEdit,
  type Leaderboard,
} from './api';
import type { ForkTarget } from './ForksSection';
import * as S from './styles';
import './duels.css';

/** One contestant as the composer edits it. */
interface Draft {
  model: string;
  temperature: string;
  system: string;
}

const EMPTY: Draft = { model: '', temperature: '', system: '' };

function edits(d: Draft): ForkEdit[] {
  const out: ForkEdit[] = [];
  if (d.model.trim()) out.push({ op: 'set_model', name: d.model.trim() });
  if (d.temperature.trim() !== '' && !Number.isNaN(Number(d.temperature)))
    out.push({ op: 'set_temperature', value: Number(d.temperature) });
  if (d.system.trim()) out.push({ op: 'set_system', content: d.system });
  return out;
}

function errorText(e: unknown): string {
  const text = e instanceof Error ? e.message : String(e);
  // The API's 400s arrive as `{"detail": "…"}`; say the sentence, not the JSON.
  const m = /"detail"\s*:\s*"([^"]+)"/.exec(text);
  return m ? m[1] : text;
}

// ── The leaderboard ──────────────────────────────────────────────────────────

function LeaderboardView({ board, highlight }: { board: Leaderboard | null; highlight: string[] }) {
  if (!board || board.contestants.length === 0) {
    return (
      <div style={{ ...S.mono, padding: '6px 0', lineHeight: 1.6 }}>
        No ratings yet. Every vote moves two of them; ties count half, “both bad” counts nothing.
      </div>
    );
  }
  const top = Math.max(...board.contestants.map((c) => c.rating));
  const low = Math.min(...board.contestants.map((c) => c.rating), board.start - 50);
  return (
    <ol className="duel-board">
      {board.contestants.map((c, i) => (
        <li
          key={c.label}
          className={`duel-board-row${highlight.includes(c.label) ? ' is-moved' : ''}`}
          style={S.stagger(i)}
          title={`${c.wins} won · ${c.losses} lost · ${c.ties} tied · ${c.both_bad} both bad`}
        >
          <span className="duel-rank">{i + 1}</span>
          <span className="duel-label">{c.label}</span>
          <span className="duel-rating">{Math.round(c.rating)}</span>
          <span className="duel-bar" aria-hidden="true">
            <span
              style={{ width: `${Math.max(4, ((c.rating - low) / (top - low || 1)) * 100)}%` }}
            />
          </span>
          <span className="duel-record">
            {c.wins}–{c.losses}
            {c.ties ? `–${c.ties}` : ''}
          </span>
        </li>
      ))}
    </ol>
  );
}

// ── One duel ─────────────────────────────────────────────────────────────────

function SideCard({
  side,
  name,
  revealed,
  verdict,
  delta,
}: {
  side: DuelSide;
  name: 'A' | 'B';
  revealed: boolean;
  verdict: 'won' | 'lost' | 'tie' | 'bad' | null;
  /** The rating change this vote caused, when it was cast just now. */
  delta?: number;
}) {
  return (
    <div className={`duel-side${verdict ? ` is-${verdict}` : ''}`}>
      <div className="duel-side-head">
        <span className="duel-side-name">Answer {name}</span>
        <span className={`duel-who${revealed ? ' is-revealed' : ''}`} key={String(revealed)}>
          {revealed ? side.label : 'hidden until you vote'}
        </span>
        {verdict && (
          <span className="duel-verdict">
            {verdict === 'bad' ? 'both bad' : verdict}
            {delta !== undefined && Math.round(delta) !== 0 && (
              <span className="duel-delta">
                {delta > 0 ? '+' : '−'}
                {Math.abs(Math.round(delta))}
              </span>
            )}
          </span>
        )}
      </div>
      {side.status !== 'complete' ? (
        <div style={{ ...S.code, color: 'var(--error)' }}>{side.error ?? side.status}</div>
      ) : (
        <>
          <div className="duel-moves">
            <span style={S.heading}>First move</span>
            {side.decision.length ? (
              side.decision.map((call, i) => (
                <span key={`${call}-${i}`} style={S.pill}>
                  {call}
                </span>
              ))
            ) : (
              <span style={S.mono}>answered without calling a tool</span>
            )}
          </div>
          {side.calls.length > side.decision.length && (
            <div className="duel-moves">
              <span style={S.heading}>Then</span>
              {side.calls.slice(side.decision.length).map((call, i) => (
                <span key={`${call}-${i}`} style={S.pill}>
                  {call}
                </span>
              ))}
            </div>
          )}
          <div className="duel-answer">{side.answer || '(no final answer)'}</div>
          <div style={S.mono}>
            {side.rounds} round{side.rounds === 1 ? '' : 's'} · {side.total_tokens.toLocaleString()}{' '}
            tokens
          </div>
        </>
      )}
    </div>
  );
}

const VERDICTS: { vote: DuelVote; label: string }[] = [
  { vote: 'a', label: 'A is better' },
  { vote: 'tie', label: 'Tie' },
  { vote: 'b', label: 'B is better' },
  { vote: 'both_bad', label: 'Both bad' },
];

function sideVerdict(
  vote: DuelVote | null,
  side: 'a' | 'b',
): 'won' | 'lost' | 'tie' | 'bad' | null {
  if (vote === null) return null;
  if (vote === 'tie') return 'tie';
  if (vote === 'both_bad') return 'bad';
  return vote === side ? 'won' : 'lost';
}

function DuelView({
  duel,
  deltas,
  onVoted,
  onStep,
  onBack,
}: {
  duel: Duel;
  deltas: Record<string, number>;
  onVoted: (duel: Duel) => void;
  onStep: (turnId: string) => void;
  onBack: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revealed = duel.vote !== null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
      <div style={S.bar}>
        <button style={S.ghostButton} onClick={onBack}>
          ← Duels
        </button>
        <span style={S.heading}>Duel</span>
        <span style={S.mono}>
          {duel.turn_id} · round {duel.from_round}
        </span>
      </div>
      <div style={{ padding: 12, overflow: 'auto', display: 'grid', gap: 12 }}>
        <div className="duel-sides">
          <SideCard
            side={duel.a}
            name="A"
            revealed={revealed}
            verdict={sideVerdict(duel.vote, 'a')}
            delta={deltas[duel.a.label]}
          />
          <SideCard
            side={duel.b}
            name="B"
            revealed={revealed}
            verdict={sideVerdict(duel.vote, 'b')}
            delta={deltas[duel.b.label]}
          />
        </div>
        {error && <div style={{ ...S.empty, color: 'var(--error)' }}>{error}</div>}
        <div className="duel-vote" role="group" aria-label="Your verdict">
          {VERDICTS.map((v) => (
            <button
              key={v.vote}
              style={duel.vote === v.vote ? S.primaryButton : S.ghostButton}
              disabled={busy}
              onClick={() => {
                setBusy(true);
                voteDuel(duel.id, v.vote)
                  .then(onVoted)
                  .catch((e: unknown) => setError(errorText(e)))
                  .finally(() => setBusy(false));
              }}
            >
              {v.label}
            </button>
          ))}
          {revealed && <span style={S.mono}>Voting again replaces your verdict.</span>}
        </div>
        {revealed && (
          <div className="duel-vote">
            <button style={S.ghostButton} onClick={() => onStep(duel.a.fork_turn_id)}>
              Step through A
            </button>
            <button style={S.ghostButton} onClick={() => onStep(duel.b.fork_turn_id)}>
              Step through B
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── The composer ─────────────────────────────────────────────────────────────

function ContestantForm({
  title,
  draft,
  onChange,
}: {
  title: string;
  draft: Draft;
  onChange: (d: Draft) => void;
}) {
  return (
    <div className="duel-contestant">
      <div style={S.heading}>{title}</div>
      <label className="duel-field">
        <span style={S.mono}>Model</span>
        <input
          style={S.control}
          list="duel-models"
          placeholder="the turn's own model"
          value={draft.model}
          onChange={(e) => onChange({ ...draft, model: e.target.value })}
        />
      </label>
      <label className="duel-field">
        <span style={S.mono}>Temperature</span>
        <input
          style={S.control}
          inputMode="decimal"
          placeholder="the turn's own"
          value={draft.temperature}
          onChange={(e) => onChange({ ...draft, temperature: e.target.value })}
        />
      </label>
      <label className="duel-field">
        <span style={S.mono}>System prompt (optional)</span>
        <textarea
          style={{ ...S.control, height: 64, padding: 8, resize: 'vertical' }}
          placeholder="the turn's own"
          value={draft.system}
          onChange={(e) => onChange({ ...draft, system: e.target.value })}
        />
      </label>
    </div>
  );
}

function Composer({
  target,
  onDone,
  onCancel,
}: {
  target: ForkTarget;
  onDone: (duel: Duel) => void;
  onCancel: () => void;
}) {
  const [one, setOne] = useState<Draft>(EMPTY);
  const [two, setTwo] = useState<Draft>(EMPTY);
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void availableModels().then(setModels);
  }, []);
  const same = JSON.stringify(edits(one)) === JSON.stringify(edits(two));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
      <div style={S.bar}>
        <span style={S.heading}>New duel</span>
        <span style={S.mono}>
          {target.turnId} · round {target.round}
        </span>
        <button style={{ ...S.ghostButton, marginLeft: 'auto' }} onClick={onCancel}>
          Cancel
        </button>
      </div>
      <div style={{ padding: 12, overflow: 'auto', display: 'grid', gap: 12 }}>
        <datalist id="duel-models">
          {models.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        <div className="duel-sides">
          <ContestantForm title="Contestant 1" draft={one} onChange={setOne} />
          <ContestantForm title="Contestant 2" draft={two} onChange={setTwo} />
        </div>
        <div style={{ ...S.mono, lineHeight: 1.6 }}>
          Both answer the same round of the recorded turn, at once, with tools simulated. Which one
          is shown as A is random, and their names stay hidden until you vote.
        </div>
        {error && <div style={{ ...S.code, color: 'var(--error)' }}>{error}</div>}
        <div className="duel-vote">
          <button
            style={S.primaryButton}
            disabled={busy || same}
            title={same ? 'The two contestants must differ' : undefined}
            onClick={() => {
              setBusy(true);
              setError(null);
              runDuel({
                turn_id: target.turnId,
                from_round: target.round,
                contestants: [edits(one), edits(two)],
              })
                .then(onDone)
                .catch((e: unknown) => setError(errorText(e)))
                .finally(() => setBusy(false));
            }}
          >
            {busy ? 'Both are answering…' : 'Start the duel'}
          </button>
          {busy && (
            <span className="duel-racing" aria-hidden="true">
              <span />
              <span />
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ── The section ──────────────────────────────────────────────────────────────

export function DuelsSection({
  target,
  onClearTarget,
  onStep,
}: {
  target: ForkTarget | null;
  onClearTarget: () => void;
  /** Open a side's turn in the stepper. */
  onStep: (turnId: string) => void;
}) {
  const [duels, setDuels] = useState<Duel[]>([]);
  const [board, setBoard] = useState<Leaderboard | null>(null);
  const [moved, setMoved] = useState<string[]>([]);
  // Rating changes from the vote just cast, by contestant label.
  const [deltas, setDeltas] = useState<Record<string, number>>({});
  const [open, setOpen] = useState<Duel | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    Promise.all([listDuels(), getLeaderboard()])
      .then(([list, lb]) => {
        setDuels(list.duels);
        setBoard(lb);
        setError(null);
      })
      .catch((e: unknown) => setError(errorText(e)));
  }, []);
  useEffect(refresh, [refresh]);

  const pending = useMemo(() => duels.filter((d) => d.vote === null).length, [duels]);

  const voted = (d: Duel) => {
    setOpen(d);
    setMoved([d.a.label, d.b.label]);
    const before = new Map(board?.contestants.map((c) => [c.label, c.rating]) ?? []);
    getLeaderboard()
      .then((after) => {
        const next: Record<string, number> = {};
        for (const label of [d.a.label, d.b.label]) {
          const now = after.contestants.find((c) => c.label === label)?.rating;
          if (now !== undefined) next[label] = now - (before.get(label) ?? after.start);
        }
        setDeltas(next);
      })
      .catch(() => setDeltas({}));
    refresh();
  };

  let main;
  if (target) {
    main = (
      <Composer
        target={target}
        onCancel={onClearTarget}
        onDone={(d) => {
          onClearTarget();
          setOpen(d);
          refresh();
        }}
      />
    );
  } else if (open) {
    main = (
      <DuelView
        duel={open}
        deltas={deltas}
        onVoted={voted}
        onStep={onStep}
        onBack={() => {
          setOpen(null);
          setDeltas({});
        }}
      />
    );
  } else {
    main = (
      <div style={S.empty}>
        A duel puts two configurations — two models, two temperatures, two system prompts — on the
        same recorded round and asks you which answer is better, without saying which is which.
        <br />
        <br />
        Open a turn in <strong>Runs</strong>, scrub to a round, and press <strong>Duel</strong>.
        Every vote moves the leaderboard on the left: a ranking on your own tasks rather than on a
        public benchmark.
      </div>
    );
  }

  return (
    <div
      style={{ display: 'grid', gridTemplateColumns: '340px minmax(0,1fr)', minHeight: 0, flex: 1 }}
    >
      <div style={{ ...S.column, display: 'flex', flexDirection: 'column' }}>
        <div style={S.bar}>
          <span style={S.heading}>Leaderboard</span>
          <span style={S.mono}>{board?.voted ?? 0} votes</span>
          <button
            style={{ ...S.ghostButton, marginLeft: 'auto' }}
            onClick={refresh}
            aria-label="Refresh"
          >
            <IconRetry width={12} height={12} />
          </button>
        </div>
        <div
          style={{
            padding: 10,
            overflow: 'auto',
            minHeight: 0,
            flex: 1,
            display: 'grid',
            gap: 12,
            alignContent: 'start',
          }}
        >
          {error && <div style={{ ...S.mono, color: 'var(--error)' }}>{error}</div>}
          <LeaderboardView board={board} highlight={moved} />
          <div style={S.heading}>
            Duels {pending > 0 && <span style={S.statusPill('warn')}>{pending} to judge</span>}
          </div>
          {duels.length === 0 && <div style={S.mono}>None yet.</div>}
          {duels.map((d, i) => (
            <div key={d.id} style={{ ...S.card(open?.id === d.id), ...S.stagger(i) }}>
              <button
                onClick={() => {
                  onClearTarget();
                  setDeltas({});
                  setOpen(d);
                }}
                style={{ all: 'unset', cursor: 'pointer', display: 'grid', gap: 4, width: '100%' }}
              >
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <span style={S.statusPill(d.vote ? 'ok' : 'warn')}>
                    {d.vote ? 'judged' : 'to judge'}
                  </span>
                  <span style={S.mono}>{new Date(d.created_at * 1000).toLocaleString()}</span>
                </div>
                <div style={S.mono}>
                  {d.vote ? `${d.a.label} vs ${d.b.label}` : 'two hidden contestants'}
                </div>
              </button>
              <button
                style={{ ...S.ghostButton, marginTop: 6 }}
                onClick={() =>
                  deleteDuel(d.id)
                    .then(() => {
                      if (open?.id === d.id) setOpen(null);
                      refresh();
                    })
                    .catch(() => refresh())
                }
              >
                Forget
              </button>
            </div>
          ))}
        </div>
      </div>
      {main}
    </div>
  );
}
