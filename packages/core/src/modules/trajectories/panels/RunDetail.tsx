/**
 * One run, walked step by step — and the place its verdict is set.
 *
 * The grading buttons live here rather than on the list rows, and that is a rule
 * rather than a preference: `DataRow` becomes a real `<button>` once it is clickable,
 * and a `<button>` may not contain interactive descendants. The list holds identities;
 * everything you *do* to a run happens in this column.
 */
import { useEffect, useState } from 'react';

import { Button, Chip } from '../../../Primitives';
import {
  addLabel,
  getRunMcp,
  type McpCallSummary,
  type TrajectoryDetail,
  type TrajectoryStep,
} from '../api';
import { CheckIcon, CircleIcon, ScaleIcon, TrashIcon, XIcon } from '../icons';
import { CommonsPublish } from './CommonsPublish';
import { IoLane } from './IoLane';
import { McpStepDetail } from './McpStepDetail';
import { Timeline } from './Timeline';
import { TraceView } from './TraceView';
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
  tokens,
  usd,
} from './common';

function StepRow({
  runId,
  step,
  index,
  selected,
  mcpCall,
  depth = 0,
}: {
  runId: string;
  step: TrajectoryStep;
  index: number;
  selected: boolean;
  depth?: number;
  /** Present when this step was an MCP tool call with a recorded summary. */
  mcpCall?: McpCallSummary;
}) {
  const [open, setOpen] = useState(false);
  const failed = step.ok === false;
  return (
    <div
      className="traj-in"
      id={`traj-step-${step.seq}`}
      style={{
        background: selected ? 'var(--bg-hover)' : undefined,
        borderLeft: `2px solid ${failed ? 'var(--danger)' : 'var(--border)'}`,
        paddingLeft: 'var(--space-4)',
        marginBottom: 'var(--space-3)',
        // A natively nested trace (received over OTLP) indents by its depth.
        marginLeft: depth ? `calc(${depth} * var(--space-5))` : undefined,
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
      {mcpCall ? <McpStepDetail runId={runId} seq={step.seq} call={mcpCall} /> : null}
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

/** Depth from `parent_seq` links, capped so a pathological chain cannot push a row
 * off the pane (and a cycle cannot loop). */
function stepDepth(steps: TrajectoryStep[], step: TrajectoryStep): number {
  const bySeq = new Map(steps.map((s) => [s.seq, s]));
  let depth = 0;
  let parent = step.parent_seq;
  while (parent != null && depth < 6) {
    depth += 1;
    parent = bySeq.get(parent)?.parent_seq;
  }
  return depth;
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
  const [selectedSeq, setSelectedSeq] = useState<number | null>(null);
  const [mcpCalls, setMcpCalls] = useState<Record<string, McpCallSummary>>({});

  // Fetched only when the run has a turn to join on and at least one step is an MCP
  // tool — most runs call none, and a request per opened run would buy nothing.
  const hasMcpStep = run.step_list.some((s) => s.name?.startsWith('mcp-'));
  useEffect(() => {
    setMcpCalls({});
    if (!run.turn_id || !hasMcpStep) return;
    let cancelled = false;
    getRunMcp(run.id)
      .then((res) => !cancelled && setMcpCalls(res.calls))
      .catch(() => {
        // The steps still render without their server-side detail; nothing to report.
      });
    return () => {
      cancelled = true;
    };
  }, [run.id, run.turn_id, hasMcpStep]);

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
  const tokenLine = tokens(run.tokens_in, run.tokens_out);

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
          {tokenLine ? <span>{tokenLine}</span> : null}
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

      {/* Not offered for a friend's run: they shared it with you, not with strangers.
          The backend refuses it too; this only avoids offering a button that cannot work. */}
      {run.source !== 'peer' ? (
        <div style={{ marginBottom: 'var(--space-5)' }}>
          <CommonsPublish runId={run.id} running={run.status === 'running'} />
        </div>
      ) : null}

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

      {/* Where the time went, then what was done with it. Both, not one: the
          waterfall is unreadable at 200 steps and the list hides latency entirely. */}
      <Timeline
        steps={run.step_list}
        startedAt={run.started_at}
        selectedSeq={selectedSeq}
        onSelect={(seq) => {
          setSelectedSeq(seq);
          document
            .getElementById(`traj-step-${seq}`)
            ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }}
      />

      {/* The span tree: nesting and concurrency, which the waterfall above cannot
          draw. Absent entirely for a run with no trace. */}
      <TraceView run={run} />

      {/* What went over the wire during the turn — read from disk, so it survives
          long after the 500-event live ring has forgotten it. */}
      <IoLane runId={run.id} hasTurn={Boolean(run.turn_id)} />

      <div style={{ ...heading, marginBottom: 'var(--space-3)' }}>
        Steps ({run.step_list.length})
      </div>
      {run.step_list.map((step, index) => (
        <StepRow
          depth={stepDepth(run.step_list, step)}
          key={step.seq}
          runId={run.id}
          step={step}
          index={index}
          selected={selectedSeq === step.seq}
          mcpCall={mcpCalls[String(step.seq)]}
        />
      ))}
    </div>
  );
}
