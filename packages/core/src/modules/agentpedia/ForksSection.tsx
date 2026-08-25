/**
 * Forks: re-run a recorded turn with one thing changed, and diff the two.
 *
 * The same intervention the lens performs on a trace, one altitude up — there the
 * edit is a token and the readout is a logit, here the edit is the context and the
 * readout is what the agent decided to do.
 *
 * Three things the screen has to say out loud, because each of them is the
 * difference between a finding and a mistake that looks like one:
 *
 * - **How faithfully the parent's context rebuilt.** Snapshots clip block previews
 *   at 4 KB. A fork that quietly ran a truncated prompt answers differently for a
 *   reason that is not the edit.
 * - **Which edits matched nothing.** A `drop_tool` naming a tool that was never
 *   offered removes nothing, and "removing it changed nothing" would be the
 *   conclusion.
 * - **Simulated vs live.** Simulated is the default and nothing acts. Live runs the
 *   tools for real through the ordinary permission gate, so it is a button you
 *   confirm, not a checkbox you forget.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { IconAlert, IconCheck, IconChevron, IconRetry } from '../../glyphs';
import {
  deleteFork,
  forkDiff,
  listForks,
  previewFork,
  runFork,
  type ForkDiff,
  type ForkEdit,
  type ForkOp,
  type ForkPreview,
  type ForkRecord,
  type RebuildReport,
  type SideDiff,
} from './api';
import * as S from './styles';

/** Where a fork starts: a turn, and the round to branch at. */
export interface ForkTarget {
  turnId: string;
  round: number;
  label?: string;
}

const OPS: { op: ForkOp; label: string; field: 'name' | 'content' | 'value' | 'keep' }[] = [
  { op: 'drop_tool', label: 'Drop tool', field: 'name' },
  { op: 'drop_group', label: 'Drop group', field: 'name' },
  { op: 'set_system', label: 'Set system prompt', field: 'content' },
  { op: 'edit_message', label: 'Edit message', field: 'content' },
  { op: 'set_model', label: 'Set model', field: 'name' },
  { op: 'set_provider', label: 'Set provider', field: 'name' },
  { op: 'set_temperature', label: 'Set temperature', field: 'value' },
  { op: 'truncate_history', label: 'Truncate history', field: 'keep' },
];

function fieldOf(op: ForkOp): 'name' | 'content' | 'value' | 'keep' {
  return OPS.find((o) => o.op === op)?.field ?? 'name';
}

function describe(edit: ForkEdit): string {
  const label = OPS.find((o) => o.op === edit.op)?.label ?? edit.op;
  const value = edit.name ?? edit.value ?? edit.keep ?? (edit.content ? '…' : '');
  return `${label}${value === '' ? '' : ` · ${value}`}`;
}

// ── The composer ─────────────────────────────────────────────────────────────

function EditRow({
  edit,
  onChange,
  onRemove,
  index,
}: {
  edit: ForkEdit;
  onChange: (next: ForkEdit) => void;
  onRemove: () => void;
  index: number;
}) {
  const field = fieldOf(edit.op);
  return (
    <div
      style={{
        ...S.stagger(index),
        display: 'flex',
        gap: 6,
        alignItems: 'center',
        marginBottom: 6,
      }}
    >
      <select
        style={{ ...S.control, width: 160 }}
        value={edit.op}
        onChange={(e) => onChange({ op: e.target.value as ForkOp })}
      >
        {OPS.map((o) => (
          <option key={o.op} value={o.op}>
            {o.label}
          </option>
        ))}
      </select>
      {edit.op === 'edit_message' && (
        <input
          style={{ ...S.control, width: 70 }}
          type="number"
          placeholder="index"
          value={edit.index ?? ''}
          onChange={(e) => onChange({ ...edit, index: Number(e.target.value) })}
        />
      )}
      <input
        style={{ ...S.control, flex: 1, minWidth: 0, fontFamily: 'var(--font-mono)' }}
        placeholder={
          field === 'name'
            ? 'name'
            : field === 'content'
              ? 'new text'
              : field === 'value'
                ? '0.7'
                : 'messages to keep'
        }
        value={String(edit[field] ?? '')}
        onChange={(e) => {
          const raw = e.target.value;
          onChange({
            ...edit,
            [field]: field === 'value' || field === 'keep' ? Number(raw) || 0 : raw,
          } as ForkEdit);
        }}
      />
      <button style={S.ghostButton} onClick={onRemove} aria-label="Remove edit">
        ✕
      </button>
    </div>
  );
}

function FidelityChips({ report }: { report: RebuildReport }) {
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      <span style={S.statusPill(report.exact ? 'ok' : 'warn')}>
        {report.exact ? <IconCheck width={11} height={11} /> : <IconAlert width={11} height={11} />}
        {report.exact ? 'context rebuilt exactly' : 'context rebuilt approximately'}
      </span>
      {report.restored.length > 0 && (
        <span style={S.statusPill('ok')} title={report.restored.join(', ')}>
          {report.restored.length} restored in full
        </span>
      )}
      {report.clipped.length > 0 && (
        <span style={S.statusPill('warn')} title={report.clipped.join(', ')}>
          {report.clipped.length} clipped
        </span>
      )}
      {report.unlinked_tool_results > 0 && (
        <span style={S.statusPill('warn')}>
          {report.unlinked_tool_results} unpaired tool results
        </span>
      )}
      {report.tool_calls_recovered > 0 && (
        <span style={S.pill}>{report.tool_calls_recovered} tool calls recovered</span>
      )}
      {report.rejected.map((why) => (
        <span key={why} style={S.statusPill('bad')} title={why}>
          <IconAlert width={11} height={11} /> {why}
        </span>
      ))}
    </div>
  );
}

function Composer({
  target,
  onDone,
}: {
  target: ForkTarget;
  onDone: (record: ForkRecord) => void;
}) {
  const [edits, setEdits] = useState<ForkEdit[]>([{ op: 'drop_tool', name: '' }]);
  const [preview, setPreview] = useState<ForkPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmLive, setConfirmLive] = useState(false);

  const request = useMemo(
    () => ({ turn_id: target.turnId, from_round: target.round, edits }),
    [target.turnId, target.round, edits],
  );

  // The preview is a read: it rebuilds the context and the catalog and runs
  // nothing, so it can follow the form as it is typed. It is also the only thing
  // that can tell you an edit matched nothing *before* you spend a model turn on
  // it.
  useEffect(() => {
    let live = true;
    previewFork(request)
      .then((res) => {
        if (live) {
          setPreview(res);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (live) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, [request]);

  const run = useCallback(
    (isLive: boolean) => {
      setBusy(true);
      setError(null);
      runFork({ ...request, live: isLive })
        .then(onDone)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => {
          setBusy(false);
          setConfirmLive(false);
        });
    },
    [request, onDone],
  );

  return (
    <div style={{ padding: 12, display: 'grid', gap: 12, overflow: 'auto' }}>
      <div>
        <div style={S.heading}>Forking</div>
        <div style={{ ...S.mono, marginTop: 4 }}>
          {target.turnId} · round {target.round}
        </div>
      </div>

      <div>
        <div style={{ ...S.heading, marginBottom: 6 }}>Edits</div>
        {edits.map((edit, i) => (
          <EditRow
            key={i}
            index={i}
            edit={edit}
            onChange={(next) => setEdits(edits.map((e, j) => (j === i ? next : e)))}
            onRemove={() => setEdits(edits.filter((_, j) => j !== i))}
          />
        ))}
        <button
          style={S.ghostButton}
          onClick={() => setEdits([...edits, { op: 'drop_tool', name: '' }])}
        >
          + Add edit
        </button>
      </div>

      {error && <div style={{ ...S.empty, color: 'var(--error)', textAlign: 'left' }}>{error}</div>}

      {preview && (
        <>
          <FidelityChips report={preview.rebuild} />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <span style={S.pill}>{preview.messages.length} messages</span>
            <span style={S.pill}>{preview.tools.length} tools offered</span>
            <span style={{ ...S.pill, fontFamily: 'var(--font-mono)' }}>
              {preview.model} @ {preview.provider}
            </span>
            {preview.drift.denied.map((name) => (
              <span key={name} style={S.statusPill('bad')}>
                −{name}
              </span>
            ))}
            {preview.drift.missing.length > 0 && (
              <span
                style={S.statusPill('warn')}
                title={`Offered to the original turn but not available now: ${preview.drift.missing.join(', ')}`}
              >
                {preview.drift.missing.length} tools no longer available
              </span>
            )}
          </div>
        </>
      )}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button style={S.primaryButton} onClick={() => run(false)} disabled={busy}>
          {busy ? 'Running…' : 'Run fork'}
        </button>
        {confirmLive ? (
          <>
            <span style={{ ...S.mono, color: 'var(--warning)' }}>
              A live fork really runs its tools. Sure?
            </span>
            <button style={S.ghostButton} onClick={() => run(true)} disabled={busy}>
              Yes, run live
            </button>
            <button style={S.ghostButton} onClick={() => setConfirmLive(false)}>
              Cancel
            </button>
          </>
        ) : (
          <button style={S.ghostButton} onClick={() => setConfirmLive(true)} disabled={busy}>
            Run live…
          </button>
        )}
        <span style={{ ...S.mono, marginLeft: 'auto' }}>
          tools simulated unless you choose live
        </span>
      </div>
    </div>
  );
}

// ── The diff ─────────────────────────────────────────────────────────────────

function Side({ side, title, tone }: { side: SideDiff; title: string; tone: 'a' | 'b' }) {
  return (
    <div style={{ ...S.column, padding: 12, display: 'grid', gap: 10, alignContent: 'start' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={S.heading}>{title}</span>
        <span style={{ ...S.mono, marginLeft: 'auto' }}>{side.model}</span>
      </div>
      <div>
        <div style={S.heading}>Decision</div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
          {side.decision.length === 0 ? (
            <span style={S.statusPill('idle')}>answered without calling anything</span>
          ) : (
            side.decision.map((name) => (
              <span
                key={name}
                style={{
                  ...S.pill,
                  fontFamily: 'var(--font-mono)',
                  borderLeft: `2px solid ${tone === 'a' ? 'var(--text-secondary)' : 'var(--accent)'}`,
                }}
              >
                {name}
              </span>
            ))
          )}
        </div>
      </div>
      <div>
        <div style={S.heading}>Calls that followed · {side.calls.length}</div>
        <div style={{ ...S.mono, marginTop: 6 }}>{side.calls.join(' → ') || '—'}</div>
      </div>
      <div>
        <div style={S.heading}>Answer</div>
        <pre style={{ ...S.code, marginTop: 6 }}>
          {side.answer ||
            '(not recorded — the final answer is only kept when trajectory capture is on)'}
        </pre>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>{side.tools_offered} tools</span>
        <span style={S.pill}>{side.rounds} rounds</span>
        <span style={S.pill}>{side.total_tokens.toLocaleString()} tokens</span>
      </div>
    </div>
  );
}

function DiffView({ diff, onBack }: { diff: ForkDiff; onBack: () => void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
      <div style={S.bar}>
        <button style={S.ghostButton} onClick={onBack}>
          <IconChevron width={12} height={12} style={{ transform: 'rotate(180deg)' }} />
          Forks
        </button>
        <span style={S.statusPill(diff.same_decision ? 'idle' : 'ok')}>
          {diff.same_decision ? 'same decision' : 'decision changed'}
        </span>
        {diff.tools_removed.map((name) => (
          <span key={name} style={S.statusPill('bad')}>
            −{name}
          </span>
        ))}
        {diff.tools_added.map((name) => (
          <span key={name} style={S.statusPill('ok')}>
            +{name}
          </span>
        ))}
        <span style={{ ...S.mono, marginLeft: 'auto' }}>
          {diff.token_delta >= 0 ? '+' : ''}
          {diff.token_delta.toLocaleString()} tokens
        </span>
      </div>
      <div style={{ ...S.bar, gap: 6, flexWrap: 'wrap' }}>
        {diff.fork.edits.map((edit, i) => (
          <span key={i} style={S.pill}>
            {describe(edit)}
          </span>
        ))}
        {diff.fork.live && <span style={S.statusPill('warn')}>ran live</span>}
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          minHeight: 0,
          flex: 1,
          overflow: 'auto',
        }}
      >
        <Side side={diff.a} title="Original" tone="a" />
        <Side side={diff.b} title="Fork" tone="b" />
      </div>
    </div>
  );
}

// ── The section ──────────────────────────────────────────────────────────────

export function ForksSection({
  target,
  onClearTarget,
}: {
  target: ForkTarget | null;
  onClearTarget: () => void;
}) {
  const [forks, setForks] = useState<ForkRecord[]>([]);
  const [diff, setDiff] = useState<ForkDiff | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listForks()
      .then((res) => setForks(res.forks))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(refresh, [refresh]);

  const open = useCallback((id: string) => {
    forkDiff(id)
      .then(setDiff)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (diff) return <DiffView diff={diff} onBack={() => setDiff(null)} />;

  return (
    <div
      style={{ display: 'grid', gridTemplateColumns: '320px minmax(0,1fr)', minHeight: 0, flex: 1 }}
    >
      <div style={{ ...S.column, display: 'flex', flexDirection: 'column' }}>
        <div style={S.bar}>
          <span style={S.heading}>Forks</span>
          <span style={S.mono}>{forks.length}</span>
          <button style={{ ...S.ghostButton, marginLeft: 'auto' }} onClick={refresh}>
            <IconRetry width={12} height={12} />
          </button>
        </div>
        <div style={{ padding: 10, overflow: 'auto', minHeight: 0, flex: 1 }}>
          {error && <div style={{ ...S.empty, color: 'var(--error)' }}>{error}</div>}
          {forks.length === 0 && !error && (
            <div style={S.empty}>
              No forks yet.
              <br />
              Open a turn in Runs and press <strong>Fork this round</strong>.
            </div>
          )}
          {forks.map((record, i) => (
            <div
              key={record.fork_turn_id}
              style={{ ...S.card(false), ...S.stagger(i), marginBottom: 6 }}
            >
              <button
                onClick={() => open(record.fork_turn_id)}
                style={{
                  all: 'unset',
                  cursor: 'pointer',
                  display: 'grid',
                  gap: 4,
                  width: '100%',
                }}
              >
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <span style={S.statusPill(record.status === 'complete' ? 'ok' : 'bad')}>
                    {record.status}
                  </span>
                  {record.live && <span style={S.statusPill('warn')}>live</span>}
                  {!record.rebuild.exact && (
                    <span style={S.statusPill('warn')} title={record.rebuild.clipped.join(', ')}>
                      approximate
                    </span>
                  )}
                </div>
                <div style={S.mono}>{record.edits.map(describe).join(' · ') || 'no edits'}</div>
                <div style={S.mono}>from {record.parent_turn_id}</div>
              </button>
              <button
                style={{ ...S.ghostButton, marginTop: 6 }}
                onClick={() =>
                  deleteFork(record.fork_turn_id)
                    .then(refresh)
                    .catch(() => refresh())
                }
              >
                Forget
              </button>
            </div>
          ))}
        </div>
      </div>

      {target ? (
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={S.bar}>
            <span style={S.heading}>New fork</span>
            <button style={{ ...S.ghostButton, marginLeft: 'auto' }} onClick={onClearTarget}>
              Cancel
            </button>
          </div>
          <Composer
            target={target}
            onDone={(record) => {
              onClearTarget();
              refresh();
              open(record.fork_turn_id);
            }}
          />
        </div>
      ) : (
        <div style={S.empty}>
          A fork re-runs a recorded turn with one thing changed — a tool dropped, the system prompt
          rewritten, a different model — and diffs the two decisions.
          <br />
          <br />
          Tools are simulated, so a turn that wrote a file or sent a message is safe to replay.
          <br />
          Open a turn in <strong>Runs</strong>, scrub to the round you want to branch at, and press{' '}
          <strong>Fork this round</strong>.
        </div>
      )}
    </div>
  );
}
