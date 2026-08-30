/**
 * One run, walked step by step — and the place its verdict is set.
 *
 * The grading buttons live here rather than on the list rows, and that is a rule
 * rather than a preference: `DataRow` becomes a real `<button>` once it is clickable,
 * and a `<button>` may not contain interactive descendants. The list holds identities;
 * everything you *do* to a run happens in this column.
 */
import { useState } from 'react';

import { Button, Chip } from '../../../Primitives';
import { addLabel, type TrajectoryDetail, type TrajectoryStep } from '../api';
import { CheckIcon, CircleIcon, ScaleIcon, TrashIcon, XIcon } from '../icons';
import {
  ago,
  card,
  heading,
  Json,
  mono,
  ms,
  outcomeKind,
  outcomeLabel,
  StepIcon,
  usd,
} from './common';

function StepRow({ step, index }: { step: TrajectoryStep; index: number }) {
  const [open, setOpen] = useState(false);
  const failed = step.ok === false;
  return (
    <div
      className="traj-in"
      style={{
        borderLeft: `2px solid ${failed ? 'var(--danger)' : 'var(--border)'}`,
        paddingLeft: 'var(--space-4)',
        marginBottom: 'var(--space-3)',
        // Capped by `--stagger-cap` in the stylesheet, so a 200-step run does not
        // take twenty seconds to finish arriving.
        ['--traj-i' as string]: Math.min(index, 12),
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
        <span style={{ color: failed ? 'var(--danger)' : 'inherit' }}>
          <StepIcon step={step} />
        </span>
        <span style={{ ...mono, minWidth: 26 }}>{step.seq}</span>
        <strong style={{ fontSize: 'var(--fs-body)' }}>{step.name ?? step.kind}</strong>
        {step.role ? <span style={mono}>{step.role}</span> : null}
        <span style={{ flex: 1 }} />
        {step.gated ? <Chip kind="warn">gated</Chip> : null}
        <span style={mono}>{ms(step.duration_ms)}</span>
        {step.args != null || step.result != null ? (
          <Button intent="ghost" size="sm" onClick={() => setOpen(!open)}>
            {open ? 'hide' : 'data'}
          </Button>
        ) : null}
      </div>
      {step.content ? (
        <div
          style={{
            fontSize: 'var(--fs-body)',
            marginTop: 'var(--space-2)',
            whiteSpace: 'pre-wrap',
            color: 'var(--text-secondary)',
          }}
        >
          {step.content}
        </div>
      ) : null}
      {step.error ? (
        <div style={{ ...mono, color: 'var(--danger)', marginTop: 'var(--space-2)' }}>
          {step.error}
        </div>
      ) : null}
      {open ? (
        <div style={{ marginTop: 'var(--space-2)' }}>
          {step.args != null ? (
            <>
              <div style={heading}>Arguments</div>
              <Json value={step.args} />
            </>
          ) : null}
          {step.result != null ? (
            <>
              <div style={{ ...heading, marginTop: 'var(--space-3)' }}>Result</div>
              <Json value={step.result} />
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function RunDetail({
  run,
  onChanged,
  onDelete,
  onInspectHarness,
}: {
  run: TrajectoryDetail;
  onChanged: () => void;
  onDelete: () => void;
  /** Jump to the Harness section with this run's fingerprint loaded. */
  onInspectHarness: (fingerprint: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  const grade = async (value: string) => {
    setBusy(true);
    try {
      await addLabel(run.id, { key: 'outcome', value, source: 'human' });
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const cost = usd(run.cost_usd);
  const tokens =
    run.tokens_in != null || run.tokens_out != null
      ? `${run.tokens_in ?? 0}↓ ${run.tokens_out ?? 0}↑ tok`
      : null;

  return (
    <div>
      <div style={{ ...card, marginBottom: 'var(--space-5)' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 'var(--space-3)',
            marginBottom: 'var(--space-3)',
          }}
        >
          <div style={{ flex: 1, fontSize: 'var(--fs-lead)', fontWeight: 600 }}>
            {run.goal || '(no goal recorded)'}
          </div>
          <Chip kind={outcomeKind(run.outcome)}>{outcomeLabel(run.outcome)}</Chip>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-5)', ...mono }}>
          <span>{run.id}</span>
          <span>{run.source}</span>
          <span>{run.model || '—'}</span>
          <span>{run.steps} steps</span>
          <span>{ms(run.duration_ms)}</span>
          {tokens ? <span>{tokens}</span> : null}
          {/* Cost only when the provider reported one. A run with no cost figure
              shows nothing rather than "$0.00", which would read as free. */}
          {cost ? <span>{cost}</span> : null}
          <span>{ago(run.started_at)}</span>
        </div>
        {run.harness ? (
          <div
            style={{
              ...mono,
              marginTop: 'var(--space-3)',
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-3)',
            }}
          >
            <span>
              harness {run.harness}
              {run.harness_detail ? ` · ${run.harness_detail.tool_names.length} tools` : ''}
            </span>
            <Button
              intent="ghost"
              size="sm"
              icon={<ScaleIcon />}
              onClick={() => onInspectHarness(run.harness as string)}
            >
              Inspect
            </Button>
          </div>
        ) : null}
        {run.turn_id ? (
          <div style={{ ...mono, marginTop: 'var(--space-1)' }}>
            turn {run.turn_id} — joins `agent_turns` for the context side
          </div>
        ) : null}
        {/* Provenance, and only when there is any: every local run would otherwise
            carry two empty fields. */}
        {run.node_id || run.person_id ? (
          <div style={{ ...mono, marginTop: 'var(--space-1)' }}>
            from {run.person_id || 'unknown person'}
            {run.node_id ? ` · node ${run.node_id}` : ''}
          </div>
        ) : null}
        {run.error ? (
          <div style={{ ...mono, color: 'var(--danger)', marginTop: 'var(--space-3)' }}>
            {run.error}
          </div>
        ) : null}
        <div
          style={{
            display: 'flex',
            gap: 'var(--space-3)',
            marginTop: 'var(--space-4)',
            alignItems: 'center',
          }}
        >
          <span style={heading}>Grade</span>
          <Button
            size="sm"
            icon={<CheckIcon />}
            disabled={busy}
            onClick={() => void grade('success')}
          >
            Success
          </Button>
          <Button size="sm" icon={<XIcon />} disabled={busy} onClick={() => void grade('failure')}>
            Failure
          </Button>
          <Button
            size="sm"
            icon={<CircleIcon />}
            disabled={busy}
            onClick={() => void grade('partial')}
          >
            Partial
          </Button>
          <span style={{ flex: 1 }} />
          <Button intent="danger" size="sm" icon={<TrashIcon />} onClick={onDelete}>
            Delete
          </Button>
        </div>
      </div>

      {run.labels.length ? (
        <div style={{ marginBottom: 'var(--space-5)' }}>
          <div style={heading}>Labels</div>
          {run.labels.map((label) => (
            <div key={label.id} style={{ ...mono, marginTop: 'var(--space-1)' }}>
              {label.key} = {label.value || label.score} · {label.source}
              {label.rationale ? ` — ${label.rationale}` : ''}
            </div>
          ))}
        </div>
      ) : null}

      <div style={{ ...heading, marginBottom: 'var(--space-3)' }}>
        Steps ({run.step_list.length})
      </div>
      {run.step_list.map((step, index) => (
        <StepRow key={step.seq} step={step} index={index} />
      ))}
    </div>
  );
}
