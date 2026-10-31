/**
 * An assistant turn's activity, drawn as it happens: runs of thoughts and tool
 * calls fold into collapsible THOUGHTS groups, the prose the agent wrote between
 * them stands on its own, and a live line underneath says what it is doing now
 * and for how long.
 *
 * The data comes from turn-steps.ts; this file only draws it. Every row is a
 * `<button>` with a class — rows are not action buttons, and the 30px control rule
 * (`button:not([class])`) must not catch them.
 */
import { useEffect, useState, type CSSProperties } from 'react';

import { IconAlert, IconCheck, IconChevron, IconClose, IconLock, IconThought } from '../../glyphs';
import { QuestionCard } from './QuestionCard';
import { argsSummary, formatDuration, toolGroup, toolLabel } from './tool-labels';
import {
  timelineBlocks,
  turnPhase,
  type ActivityBlock,
  type Answers,
  type ActivityTurn,
  type Step,
  type ThoughtStep,
  type ToolStep,
} from './turn-steps';

/** Rows past this index arrive together: a reloaded turn with forty calls should
 *  not take two seconds to finish sliding in. */
const STAGGER_CAP = 8;

function stagger(i: number): CSSProperties {
  return { ['--agent-step-i' as string]: Math.min(i, STAGGER_CAP) };
}

/** A value that re-reads the clock once a second while `active`. setInterval, not
 *  requestAnimationFrame: rAF stops in a background tab, and an elapsed counter
 *  that freezes when you look away and jumps when you look back reads as broken. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

function Spinner() {
  return <span className="agent-spinner" aria-hidden="true" />;
}

function StatusGlyph({ step }: { step: ToolStep }) {
  switch (step.status) {
    case 'running':
      return <Spinner />;
    case 'ok':
      return <IconCheck />;
    case 'approval':
    case 'denied':
      return <IconLock />;
    case 'error':
      return <IconAlert />;
    case 'interrupted':
      return <IconClose />;
  }
}

const STATUS_TEXT: Record<ToolStep['status'], string> = {
  running: 'running',
  approval: 'waiting for your approval',
  ok: 'done',
  error: 'failed',
  denied: 'not allowed',
  interrupted: 'stopped',
};

function pretty(value: unknown): string {
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function ToolRow({ step, index }: { step: ToolStep; index: number }) {
  const [open, setOpen] = useState(false);
  const label = step.label ?? toolLabel(step.name);
  const group = step.label ? null : toolGroup(step.name);
  const summary = argsSummary(step.args);
  const expandable = !step.label;
  return (
    <li className="agent-step-item" style={stagger(index)}>
      <button
        type="button"
        className="agent-step"
        data-status={step.status}
        aria-expanded={expandable ? open : undefined}
        disabled={!expandable}
        title={`${label}: ${STATUS_TEXT[step.status]}`}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="agent-step-glyph">
          <StatusGlyph step={step} />
        </span>
        <span className="agent-step-label">{label}</span>
        {group && <span className="agent-step-group">{group}</span>}
        {summary && <span className="agent-step-args">{summary}</span>}
        {step.status === 'approval' && <span className="agent-step-flag">needs approval</span>}
        {step.durationMs !== undefined && (
          <span className="agent-step-time">{formatDuration(step.durationMs)}</span>
        )}
        {expandable && <IconChevron className="hd-icon agent-step-chev" data-open={open} />}
      </button>
      {open && (
        <div className="agent-step-detail">
          <div className="agent-step-detail-head">{step.name}</div>
          {step.args !== null && step.args !== undefined && (
            <pre className="agent-step-pre">{pretty(step.args)}</pre>
          )}
          {step.preview !== undefined && (
            <>
              <div className="agent-step-detail-head">result</div>
              <pre className="agent-step-pre">{pretty(step.preview)}</pre>
            </>
          )}
        </div>
      )}
    </li>
  );
}

function ThoughtRow({ step, index, now }: { step: ThoughtStep; index: number; now: number }) {
  const thinking = step.endedAt === undefined;
  // Open while the model is still thinking, so the text can be read as it streams;
  // folded once it moves on. A row the user opened stays open.
  const [open, setOpen] = useState(thinking);
  const [wasThinking, setWasThinking] = useState(thinking);
  if (thinking !== wasThinking) {
    setWasThinking(thinking);
    if (!thinking) setOpen(false);
  }
  const ms = (step.endedAt ?? now) - step.startedAt;
  // Saved before the timeline existed: there is no duration to report.
  const timed = step.startedAt > 0;
  return (
    <li className="agent-step-item" style={stagger(index)}>
      <button
        type="button"
        className="agent-step agent-step-thought"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="agent-step-glyph">
          <IconThought />
        </span>
        <span className="agent-step-label">
          {thinking ? 'Thinking' : timed ? `Thought for ${formatDuration(ms)}` : 'Reasoning'}
        </span>
        <IconChevron className="hd-icon agent-step-chev" data-open={open} />
      </button>
      {open && <div className="agent-thought-body">{step.text}</div>}
    </li>
  );
}

function groupDuration(steps: ActivityBlock['steps'], now: number): number | null {
  const first = steps[0];
  if (!first || first.startedAt <= 0) return null;
  const last = steps[steps.length - 1];
  const end =
    last.kind === 'thought'
      ? (last.endedAt ?? now)
      : last.durationMs !== undefined
        ? last.startedAt + last.durationMs
        : now;
  return end - first.startedAt;
}

function ActivityGroup({
  block,
  live,
  now,
}: {
  block: ActivityBlock;
  /** This is the group the running turn is adding to. */
  live: boolean;
  now: number;
}) {
  const [open, setOpen] = useState(live);
  const [wasLive, setWasLive] = useState(live);
  if (live !== wasLive) {
    setWasLive(live);
    setOpen(live);
  }
  const tools = block.steps.filter((s) => s.kind === 'tool').length;
  const ms = groupDuration(block.steps, now);
  const meta = [
    tools ? `${tools} ${tools === 1 ? 'call' : 'calls'}` : null,
    ms !== null ? formatDuration(ms) : null,
  ]
    .filter(Boolean)
    .join(' · ');
  const failed = block.steps.some(
    (s) => s.kind === 'tool' && (s.status === 'error' || s.status === 'denied'),
  );
  return (
    <details
      className="agent-activity"
      open={open}
      data-live={live || undefined}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary className="agent-activity-head">
        <IconChevron className="hd-icon agent-activity-chev" />
        <span className="agent-activity-title">Thoughts</span>
        {live && <Spinner />}
        {meta && <span className="agent-activity-meta">{meta}</span>}
        {failed && !live && (
          <span className="agent-activity-flag" title="A call failed or was not allowed">
            <IconAlert />
          </span>
        )}
      </summary>
      <ol className="agent-steps">
        {block.steps.map((s, i) =>
          s.kind === 'tool' ? (
            <ToolRow key={i} step={s} index={i} />
          ) : (
            <ThoughtRow key={i} step={s} index={i} now={now} />
          ),
        )}
      </ol>
    </details>
  );
}

export function ActivityTimeline({
  steps,
  live,
  onAnswer,
}: {
  steps: Step[];
  /** The turn is still running. */
  live: boolean;
  onAnswer: (questionId: string, answers: Answers | null) => void;
}) {
  const now = useNow(live);
  const blocks = timelineBlocks(steps);
  if (blocks.length === 0) return null;
  return (
    <div className="agent-timeline">
      {blocks.map((b, i) => {
        if (b.kind === 'note') {
          return (
            <p key={i} className="agent-note">
              {b.text}
            </p>
          );
        }
        if (b.kind === 'question') {
          return <QuestionCard key={i} step={b} onAnswer={onAnswer} />;
        }
        return <ActivityGroup key={i} block={b} live={live && i === blocks.length - 1} now={now} />;
      })}
    </div>
  );
}

/** What a phase reads as under the turn. */
function phaseText(turn: ActivityTurn): string {
  const phase = turnPhase(turn);
  switch (phase.kind) {
    case 'thinking':
      return 'Thinking';
    case 'reasoning':
      return 'Reasoning';
    case 'writing':
      return 'Writing';
    case 'tool':
      return `Running ${toolLabel(phase.name)}`;
    case 'approval':
      return `Waiting for your approval: ${toolLabel(phase.name)}`;
    case 'question':
      return 'Waiting for your answer';
  }
}

/** The running turn's status line: what it is doing, and for how long. */
export function LiveLine({ turn }: { turn: ActivityTurn }) {
  const now = useNow(true);
  const seconds = turn.startedAt ? Math.max(0, Math.floor((now - turn.startedAt) / 1000)) : null;
  const waiting = ['approval', 'question'].includes(turnPhase(turn).kind);
  return (
    <div className="agent-live" role="status" data-waiting={waiting || undefined}>
      {waiting ? <IconLock /> : <Spinner />}
      <span className="agent-live-text">{phaseText(turn)}…</span>
      {seconds !== null && <span className="agent-live-time">({seconds}s)</span>}
    </div>
  );
}

/** "Worked for 2m 11s" under a finished turn that did any work. */
export function WorkedFor({ turn }: { turn: ActivityTurn }) {
  if (!turn.startedAt || !turn.endedAt) return null;
  return <p className="agent-worked">Worked for {formatDuration(turn.endedAt - turn.startedAt)}</p>;
}
