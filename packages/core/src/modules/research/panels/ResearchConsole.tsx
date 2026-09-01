/**
 * Deep-research console (`research.console`, singleton) — start runs, watch the
 * plan/steps stream live over the `research` /ws channel, read the report.
 *
 * The run list and step snapshots come from the shared store (HTTP seed + ws
 * upserts); the report tab fetches the finished artifact's markdown. Citation
 * markers stay plain text in v1 — the numbered References section carries the
 * links.
 */
import { useEffect, useMemo, useState } from 'react';

import { apiUrl } from '../../../origin';
import { registry } from '../../../registry';
import { toastsStore } from '../../../toasts';
import { getSetting } from '../../../settings';
import {
  addFollowup,
  approvePlan,
  cancelRun,
  retryRun,
  startRun,
  type RunModel,
  type StepModel,
} from '../api';
import { loadSteps, useResearchState, type ToolCallEvent } from '../store';

const STATUS_ICON: Record<string, string> = {
  pending: '…',
  planning: '🧭',
  awaiting_plan: '✋',
  researching: '🔎',
  synthesizing: '✍',
  verifying: '⚖',
  citing: '🔗',
  exporting: '📦',
  done: '✅',
  failed: '❌',
  cancelled: '⛔',
};

const STEP_ICON: Record<string, string> = {
  pending: '·',
  running: '▶',
  done: '✓',
  failed: '✗',
  skipped: '⤼',
};

/** The peer that produced this step, or '' when it ran here. */
function ranOn(step: StepModel): string {
  const output = step.output as Record<string, unknown> | null | undefined;
  return typeof output?.ran_on === 'string' ? output.ran_on : '';
}

/** What a step's body shows when expanded, by kind. */
function stepBody(step: StepModel): string {
  if (!step.output) return '';
  const output = step.output as Record<string, unknown>;
  if (step.kind === 'subagent') return String(output.findings ?? '');
  if (step.kind === 'critique') {
    const gaps = (output.gaps as string[] | undefined) ?? [];
    const next = (output.subagents as { objective?: string }[] | undefined) ?? [];
    const lines = [
      output.sufficient ? 'Findings judged sufficient.' : 'Gaps found:',
      ...gaps.map((g) => `· ${g}`),
      ...(next.length ? ['', 'Next round:'] : []),
      ...next.map((s) => `→ ${s.objective ?? ''}`),
    ];
    return lines.join('\n');
  }
  if (step.kind === 'verify') {
    const claims = (output.claims as { claim: string; verdict: string }[] | undefined) ?? [];
    const conflicts = (output.contradictions as { topic: string }[] | undefined) ?? [];
    const problems = claims.filter((c) => c.verdict !== 'supported');
    if (!problems.length && !conflicts.length) {
      return `All ${claims.length} audited claim(s) rest on two or more independent publishers.`;
    }
    return [
      ...problems.map((c) => `${c.verdict}: ${c.claim}`),
      ...conflicts.map((c) => `contradiction: ${c.topic}`),
    ].join('\n');
  }
  return '';
}

/**
 * The live tool trace under a subagent.
 *
 * This is the answer to "is it making progress or spinning" — visible while the
 * step runs, rather than only once its transcript is persisted at the end.
 */
function ToolTrace({ calls }: { calls: ToolCallEvent[] }) {
  return (
    <div style={{ margin: '0.25rem 0 0 1.4rem', fontSize: '0.72rem' }}>
      {calls.map((call) => (
        <div key={call.seq} style={{ display: 'flex', gap: '0.4rem', color: 'var(--text-dim)' }}>
          <span style={{ color: call.ok ? 'inherit' : 'var(--danger, #d66)' }}>
            {call.ok ? '›' : '✗'}
          </span>
          <span style={{ fontWeight: 600 }}>{call.name}</span>
          <span
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              flex: 1,
            }}
            title={JSON.stringify(call.args)}
          >
            {String(call.args.query ?? call.args.url ?? '')}
          </span>
          <span style={{ whiteSpace: 'nowrap' }}>{call.summary}</span>
        </div>
      ))}
    </div>
  );
}

function StepRow({
  step,
  delta,
  calls,
}: {
  step: StepModel;
  delta?: string;
  calls?: ToolCallEvent[];
}) {
  const [open, setOpen] = useState(false);
  const body = stepBody(step);
  const live = step.status === 'running' && delta ? delta : '';
  // The trace stays visible while the step runs without needing a click — that's
  // exactly when it's worth seeing.
  const showTrace = Boolean(calls?.length) && (open || step.status === 'running');
  return (
    <div style={{ borderBottom: '1px solid var(--border)', padding: '0.3rem 0.5rem' }}>
      <div
        onClick={() => setOpen((o) => !o)}
        style={{ cursor: 'pointer', display: 'flex', gap: '0.5rem', fontSize: '0.8rem' }}
      >
        <span>{STEP_ICON[step.status] ?? '·'}</span>
        <span style={{ fontWeight: 600 }}>{step.name}</span>
        <span style={{ color: 'var(--text-dim)' }}>
          {step.kind}
          {step.round > 0 ? ` · round ${step.round + 1}` : ''}
          {step.attempt > 1 ? ` · attempt ${step.attempt}` : ''}
          {step.tokens_used ? ` · ~${step.tokens_used} tok` : ''}
        </span>
        {/* Only when a friend's node answered. The verification pass grades by
            independent publisher, so two peers citing one domain are one source —
            which is only checkable if the step says where it ran. */}
        {ranOn(step) ? (
          <span
            style={{
              fontFamily: 'var(--font-mono, ui-monospace, monospace)',
              fontSize: '0.7rem',
              color: 'var(--text-secondary, var(--text-dim))',
            }}
          >
            ran on {ranOn(step)}
          </span>
        ) : null}
        {step.error && (
          <span style={{ color: 'var(--danger, #d66)', marginLeft: 'auto' }}>{step.error}</span>
        )}
      </div>
      {showTrace && <ToolTrace calls={calls ?? []} />}
      {(open || live) && (body || live || step.error) && (
        <pre
          style={{
            whiteSpace: 'pre-wrap',
            fontSize: '0.75rem',
            margin: '0.3rem 0 0 1.4rem',
            maxHeight: '14rem',
            overflow: 'auto',
            color: 'var(--text-dim)',
          }}
        >
          {live || body || step.error}
        </pre>
      )}
    </div>
  );
}

/** The gate: nothing has been spent yet, and the plan is still editable. */
function PlanGate({ run }: { run: RunModel }) {
  const [busy, setBusy] = useState(false);
  const release = () => {
    setBusy(true);
    approvePlan(run.id)
      .catch((err: unknown) => toastsStore.add('warning', 'Could not start the run', String(err)))
      .finally(() => setBusy(false));
  };
  return (
    <div
      style={{
        padding: '0.5rem 0.6rem',
        borderBottom: '1px solid var(--border)',
        fontSize: '0.78rem',
      }}
    >
      <strong>Review the plan before it runs.</strong>{' '}
      <span style={{ color: 'var(--text-dim)' }}>
        {run.plan?.subagents.length ?? 0} subagent(s), {run.plan?.complexity} effort. Nothing has
        been spent yet.
      </span>
      <div style={{ marginTop: '0.4rem' }}>
        <button type="button" disabled={busy} onClick={release}>
          Approve &amp; run
        </button>
      </div>
    </div>
  );
}

/** Ask a running investigation something extra; it shapes the next round. */
function FollowupBar({ run }: { run: RunModel }) {
  const [text, setText] = useState('');
  const send = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setText('');
    void addFollowup(run.id, trimmed)
      .then(() =>
        toastsStore.add('info', 'Follow-up queued', 'It will steer the next round of this run.'),
      )
      .catch((err: unknown) => toastsStore.add('warning', 'Could not add follow-up', String(err)));
  };
  return (
    <div style={{ display: 'flex', gap: '0.4rem', padding: '0.4rem 0.5rem' }}>
      <input
        style={{ flex: 1 }}
        placeholder="Also look into…"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && send()}
      />
      <button type="button" onClick={send}>
        add
      </button>
    </div>
  );
}

function RunDetail({ run }: { run: RunModel }) {
  const { steps, deltas, toolCalls } = useResearchState();
  const runSteps = steps[run.id] ?? [];
  const [report, setReport] = useState<string | null>(null);
  const [tab, setTab] = useState<'steps' | 'report'>('steps');

  useEffect(() => {
    loadSteps(run.id);
  }, [run.id]);

  useEffect(() => {
    if (run.status === 'done' && run.report_artifact_id) {
      setTab('report');
      void fetch(apiUrl(`/api/research/runs/${run.id}/report`))
        .then((res) => (res.ok ? res.text() : Promise.reject(new Error(String(res.status)))))
        .then(setReport)
        .catch(() => setReport(null));
    }
  }, [run.status, run.report_artifact_id, run.id]);

  const synthesisStep = runSteps.find((s) => s.kind === 'synthesis');
  const liveDelta = synthesisStep ? deltas[synthesisStep.id] : undefined;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
      <div
        style={{
          display: 'flex',
          gap: '0.5rem',
          alignItems: 'center',
          padding: '0.4rem 0.5rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.8rem',
        }}
      >
        <span>{STATUS_ICON[run.status] ?? ''}</span>
        <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {run.query}
        </span>
        <span style={{ color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
          {run.status} · {run.effort}
          {run.tokens_used ? ` · ~${run.tokens_used}/${run.token_budget} tok` : ''}
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.4rem' }}>
          <button onClick={() => setTab('steps')} disabled={tab === 'steps'}>
            steps
          </button>
          <button
            onClick={() => setTab('report')}
            disabled={tab === 'report' || !run.report_artifact_id}
          >
            report
          </button>
          {!['done', 'failed', 'cancelled'].includes(run.status) && (
            <button
              onClick={() =>
                void cancelRun(run.id).catch((err: unknown) =>
                  toastsStore.add('warning', 'Cancel failed', String(err)),
                )
              }
            >
              cancel
            </button>
          )}
          {['failed', 'cancelled'].includes(run.status) && (
            <button
              onClick={() =>
                void retryRun(run.id).catch((err: unknown) =>
                  toastsStore.add('warning', 'Retry failed', String(err)),
                )
              }
            >
              retry
            </button>
          )}
        </span>
      </div>
      {run.error && (
        <div
          style={{ padding: '0.4rem 0.6rem', color: 'var(--danger, #d66)', fontSize: '0.78rem' }}
        >
          {run.error}
        </div>
      )}
      {run.status === 'awaiting_plan' && <PlanGate run={run} />}
      {tab === 'steps' ? (
        <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
          <div style={{ flex: 1, overflow: 'auto' }}>
            {run.plan && (
              <div
                style={{ padding: '0.4rem 0.6rem', fontSize: '0.75rem', color: 'var(--text-dim)' }}
              >
                plan: {run.plan.complexity} · {run.plan.subagents.length} subagent(s)
                {run.rounds_used > 1 ? ` · ${run.rounds_used} rounds` : ''}
              </div>
            )}
            {runSteps.map((step) => (
              <StepRow
                key={step.id}
                step={step}
                delta={step.kind === 'synthesis' ? liveDelta : undefined}
                calls={toolCalls[step.id]}
              />
            ))}
            {runSteps.length === 0 && (
              <div style={{ padding: '0.75rem', color: 'var(--text-dim)', fontSize: '0.8rem' }}>
                Waiting for the plan…
              </div>
            )}
          </div>
          {!['done', 'failed', 'cancelled', 'awaiting_plan'].includes(run.status) && (
            <FollowupBar run={run} />
          )}
        </div>
      ) : (
        <div style={{ flex: 1, overflow: 'auto', padding: '0.75rem' }}>
          {run.report_source_id && (
            <div style={{ marginBottom: '0.5rem', display: 'flex', gap: '0.5rem' }}>
              <button
                onClick={() =>
                  registry.openPanel('library.panel', {
                    params: { sourceId: run.report_source_id },
                  })
                }
              >
                in library ↗
              </button>
            </div>
          )}
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: '0.83rem', lineHeight: 1.5 }}>
            {report ?? liveDelta ?? 'No report yet.'}
          </pre>
        </div>
      )}
    </div>
  );
}

export function ResearchConsole() {
  const { runs } = useResearchState();
  const [query, setQuery] = useState('');
  const [effort, setEffort] = useState('auto');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [reviewPlan, setReviewPlan] = useState(false);

  const selected = useMemo(
    () => runs.find((r) => r.id === selectedId) ?? runs[0] ?? null,
    [runs, selectedId],
  );

  const begin = () => {
    const trimmed = query.trim();
    if (!trimmed || starting) return;
    setStarting(true);
    startRun({
      query: trimmed,
      effort,
      library: getSetting<string>('browser.saveLibrary') || 'default',
      approval_mode: reviewPlan ? 'plan' : 'auto',
    })
      .then((run) => {
        setSelectedId(run.id);
        setQuery('');
      })
      .catch((err: unknown) =>
        toastsStore.add(
          'warning',
          'Could not start run',
          err instanceof Error ? err.message : String(err),
        ),
      )
      .finally(() => setStarting(false));
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          display: 'flex',
          gap: '0.5rem',
          padding: '0.4rem 0.5rem',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <input
          value={query}
          placeholder="Research a topic — a durable multi-agent run with a cited report…"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') begin();
          }}
          style={{ flex: 1 }}
        />
        <select value={effort} onChange={(e) => setEffort(e.target.value)}>
          <option value="auto">auto</option>
          <option value="quick">quick</option>
          <option value="standard">standard</option>
          <option value="deep">deep</option>
        </select>
        <label
          title="Pause after planning so you can see (and change) what it intends to do before any subagent spends a token."
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.25rem',
            fontSize: '0.75rem',
            color: 'var(--text-dim)',
            whiteSpace: 'nowrap',
          }}
        >
          <input
            type="checkbox"
            checked={reviewPlan}
            onChange={(e) => setReviewPlan(e.target.checked)}
          />
          review plan
        </label>
        <button onClick={begin} disabled={starting || !query.trim()}>
          {starting ? '…' : 'Research'}
        </button>
      </div>
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <div
          style={{
            flex: '0 0 240px',
            overflow: 'auto',
            borderRight: '1px solid var(--border)',
          }}
        >
          {runs.map((run) => (
            <div
              key={run.id}
              onClick={() => setSelectedId(run.id)}
              style={{
                padding: '0.45rem 0.55rem',
                cursor: 'pointer',
                borderBottom: '1px solid var(--border)',
                background:
                  selected?.id === run.id ? 'var(--bg-hover, rgba(128,128,128,0.15))' : undefined,
              }}
            >
              <div
                style={{
                  fontSize: '0.78rem',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {STATUS_ICON[run.status] ?? ''} {run.query}
              </div>
              <div style={{ fontSize: '0.68rem', color: 'var(--text-dim)' }}>
                {run.status} · {run.created_at.slice(0, 16)}
              </div>
            </div>
          ))}
          {runs.length === 0 && (
            <div style={{ padding: '0.75rem', fontSize: '0.78rem', color: 'var(--text-dim)' }}>
              No runs yet. Ask a research question above — the run survives backend restarts and
              files its report into the library.
            </div>
          )}
        </div>
        {selected ? <RunDetail run={selected} /> : <div style={{ flex: 1 }} />}
      </div>
    </div>
  );
}
