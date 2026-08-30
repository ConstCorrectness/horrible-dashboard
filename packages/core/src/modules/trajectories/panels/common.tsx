/**
 * Formatting and small shared pieces for the trajectories sections.
 *
 * Everything visual here goes through the theme scale (`--space-*`, `--fs-*`,
 * `--radius-*`). The pane used to carry a local `const S` object of raw pixel values
 * and legacy alias tokens, and the aliases were the interesting failure: an undefined
 * `var()` falls through to its hex fallback, so under `data-theme="daylight"` this pane
 * rendered fully dark while everything beside it rendered light. `no-hex-literals.test.ts`
 * holds these files at **zero** hardcoded colours for exactly that reason.
 */
import type { CSSProperties, ReactNode } from 'react';

import type { RowKind } from '../../../DataList';
import type { Outcome, TrajectoryStep } from '../api';
import {
  AlertIcon,
  BrainIcon,
  EyeIcon,
  LockIcon,
  MessageIcon,
  TerminalIcon,
  TrophyIcon,
  XIcon,
} from '../icons';

/**
 * An outcome as a row verdict.
 *
 * `unknown` and `null` both mean **ungraded**, and both map to `idle` rather than to
 * anything that looks like a result. A run nobody has judged has not failed, and
 * colouring it as though it had is the same lie `analyze.py` refuses to tell when it
 * returns a null success rate instead of zero.
 */
export function outcomeKind(outcome: Outcome | null): RowKind {
  if (outcome === 'success') return 'ok';
  if (outcome === 'failure') return 'fail';
  if (outcome === 'partial') return 'warn';
  return 'idle';
}

export function outcomeLabel(outcome: Outcome | null): string {
  return outcome && outcome !== 'unknown' ? outcome : 'ungraded';
}

export function ms(value: number | null | undefined): string {
  if (value == null) return '—';
  return value < 1000 ? `${Math.round(value)}ms` : `${(value / 1000).toFixed(1)}s`;
}

export function usd(value: number | null | undefined): string | null {
  if (value == null) return null;
  return value < 0.01 ? `<$0.01` : `$${value.toFixed(2)}`;
}

export function ago(seconds: number): string {
  const delta = Date.now() / 1000 - seconds;
  if (delta < 60) return 'just now';
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

/** Monospace telemetry, at the scale's metadata size. */
export const mono: CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: 'var(--fs-meta)',
  color: 'var(--text-secondary)',
};

export const heading: CSSProperties = {
  fontSize: 'var(--fs-label)',
  fontWeight: 700,
  letterSpacing: 'var(--tracking-display)',
  textTransform: 'uppercase',
  color: 'var(--text-secondary)',
};

/**
 * A card. The 2px accent edge is on the **top only** — the house rule, in place of a
 * glowing full perimeter.
 */
export const card: CSSProperties = {
  border: '1px solid var(--border)',
  borderTop: '2px solid var(--accent)',
  borderRadius: 'var(--radius-sm)',
  padding: 'var(--space-5)',
  background: 'var(--bg-secondary)',
};

export const bodyScroll: CSSProperties = {
  flex: 1,
  overflow: 'auto',
  minHeight: 0,
};

/** The wrapper every section shares: header, optional error, then the body. */
export function SectionShell({
  header,
  error,
  children,
}: {
  header: ReactNode;
  error?: string;
  children: ReactNode;
}) {
  return (
    <>
      {header}
      {/* `role="alert"` because a failed fetch is the one thing here a screen reader
          has no other way to learn about — the list simply stays empty. */}
      {error ? (
        <div
          role="alert"
          style={{
            ...mono,
            color: 'var(--danger)',
            padding: 'var(--space-3) var(--space-5)',
          }}
        >
          {error}
        </div>
      ) : null}
      {children}
    </>
  );
}

/**
 * The first fetch, before anything is known.
 *
 * Every section used to render its *empty state* while loading, so "No trajectories
 * yet — capture is off by default" flashed on every mount and read as a real answer to
 * a question nobody had finished asking.
 */
export function Loading({ what }: { what: string }) {
  return (
    <div
      style={{
        padding: 'var(--space-6)',
        color: 'var(--text-dim)',
        fontSize: 'var(--fs-body)',
      }}
    >
      Loading {what}…
    </div>
  );
}

export function StepIcon({ step }: { step: TrajectoryStep }) {
  if (step.kind === 'action') {
    if (step.gated) return <LockIcon />;
    return step.ok === false ? <XIcon /> : <TerminalIcon />;
  }
  if (step.kind === 'thought') return <BrainIcon />;
  if (step.kind === 'observation') return <EyeIcon />;
  if (step.kind === 'reward') return <TrophyIcon />;
  if (step.kind === 'error') return <AlertIcon />;
  return <MessageIcon />;
}

export function Json({ value }: { value: unknown }) {
  if (value == null) return null;
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return (
    <pre
      style={{
        ...mono,
        margin: 'var(--space-2) 0 0',
        padding: 'var(--space-3)',
        background: 'var(--bg-primary)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-sm)',
        maxHeight: 220,
        overflow: 'auto',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}
    >
      {text}
    </pre>
  );
}

/** A right-aligned monospace figure, for the stat tables. */
export function Figure({
  children,
  tone,
  width = 70,
}: {
  children: ReactNode;
  tone?: 'warn' | 'fail' | 'accent';
  width?: number;
}) {
  return (
    <span
      style={{
        minWidth: width,
        textAlign: 'right',
        color:
          tone === 'fail'
            ? 'var(--danger)'
            : tone === 'warn'
              ? 'var(--warning)'
              : tone === 'accent'
                ? 'var(--accent)'
                : 'inherit',
      }}
    >
      {children}
    </span>
  );
}
