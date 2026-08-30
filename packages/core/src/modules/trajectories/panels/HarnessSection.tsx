/**
 * "Did my change help?" — and the answer "these two never ran the same tasks".
 *
 * The section that justifies the module. Two things it must never do: report a
 * comparison when the two harnesses share too few goals for the difference to mean
 * anything (`comparable: false` is a first-class verdict here, not a footnote), and
 * render an ungraded success rate as `0%`. `CompareSide.successRate` is
 * `number | null` on purpose, and null means nobody has judged these runs.
 *
 * The inspector is the other half: a fingerprint is a hash, and "harness a3f2 beats
 * harness 91cd" is unusable without being able to see what either one *was*.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { RollingNumber } from '../../../DataList';
import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { ControlBar } from '../../../ResourceCard';
import {
  compareHarnesses,
  getHarness,
  listHarnesses,
  type CompareReport,
  type CompareSide,
  type Harness,
} from '../api';
import { ScaleIcon } from '../icons';
import { bodyScroll, card, Figure, heading, Json, Loading, mono, SectionShell } from './common';

function SideCard({ side, role }: { side: CompareSide; role: string }) {
  return (
    <div style={{ ...card, flex: 1, minWidth: 220 }}>
      <div style={heading}>{role}</div>
      <div style={{ fontSize: 'var(--fs-body)', fontWeight: 600, marginTop: 'var(--space-1)' }}>
        {side.label}
      </div>
      <div
        style={{
          fontSize: 'var(--fs-display)',
          fontFamily: 'var(--font-mono)',
          marginTop: 'var(--space-3)',
        }}
      >
        {side.successRate == null ? (
          // Never `0%`. Nothing graded is not the same fact as nothing passing, and
          // the backend went out of its way to return null rather than a zero.
          <span style={{ fontSize: 'var(--fs-body)', color: 'var(--text-dim)' }}>ungraded</span>
        ) : (
          <RollingNumber
            value={side.successRate * 100}
            format={(n) => `${Math.round(n)}%`}
          />
        )}
      </div>
      <div style={mono}>
        {side.runs} runs · {side.graded} graded · {side.avgSteps} steps avg
      </div>
      <div style={mono}>{side.fingerprint}</div>
    </div>
  );
}

function GoalList({ title, tone, goals }: { title: string; tone: string; goals: string[] }) {
  if (goals.length === 0) return null;
  return (
    <div style={{ marginBottom: 'var(--space-5)' }}>
      <div style={{ ...heading, color: tone }}>{title}</div>
      {goals.map((goal) => (
        <div key={goal} style={{ ...mono, marginTop: 'var(--space-1)' }}>
          {goal}
        </div>
      ))}
    </div>
  );
}

function Inspector({ harness }: { harness: Harness }) {
  return (
    <div style={{ ...card, marginBottom: 'var(--space-6)' }}>
      <div style={heading}>Harness {harness.fingerprint}</div>
      <div style={{ fontSize: 'var(--fs-body)', fontWeight: 600, marginTop: 'var(--space-1)' }}>
        {harness.label}
      </div>
      <div style={{ ...mono, marginTop: 'var(--space-2)' }}>
        {harness.provider} · {harness.model} · {harness.run_count} runs ·{' '}
        {harness.tool_names.length} tools
      </div>
      <div style={{ ...heading, marginTop: 'var(--space-4)' }}>Tools</div>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--space-2)',
          marginTop: 'var(--space-2)',
        }}
      >
        {harness.tool_names.length === 0 ? (
          <span style={mono}>none recorded</span>
        ) : (
          harness.tool_names.map((tool) => (
            <Chip key={tool} kind="info">
              {tool}
            </Chip>
          ))
        )}
      </div>
      {harness.system_prompt ? (
        <>
          <div style={{ ...heading, marginTop: 'var(--space-4)' }}>System prompt</div>
          <Json value={harness.system_prompt} />
        </>
      ) : null}
      {Object.keys(harness.params).length ? (
        <>
          <div style={{ ...heading, marginTop: 'var(--space-4)' }}>Sampling</div>
          <Json value={harness.params} />
        </>
      ) : null}
    </div>
  );
}

export function HarnessSection({ inspect }: { inspect?: string }) {
  const [harnesses, setHarnesses] = useState<Harness[]>([]);
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [report, setReport] = useState<CompareReport | null>(null);
  const [detail, setDetail] = useState<Harness | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    listHarnesses()
      .then(setHarnesses)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  const load = useCallback(async (fingerprint: string) => {
    try {
      setDetail(await getHarness(fingerprint));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // A run's "Inspect" button routes here with its fingerprint; load it and preselect
  // it as the baseline, which is almost always the next thing wanted.
  useEffect(() => {
    if (!inspect) return;
    setA(inspect);
    void load(inspect);
  }, [inspect, load]);

  const run = async () => {
    if (!a || !b) return;
    try {
      setReport(await compareHarnesses(a, b));
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const options = useMemo(
    () =>
      harnesses.map((h) => (
        <option key={h.fingerprint} value={h.fingerprint}>
          {h.label} · {h.fingerprint} ({h.run_count} runs)
        </option>
      )),
    [harnesses],
  );

  const header = (
    <PaneHeader
      title="Harness"
      meta={[`${harnesses.length} recorded`]}
      actions={
        a ? (
          <Button intent="ghost" size="sm" onClick={() => void load(a)}>
            Inspect baseline
          </Button>
        ) : undefined
      }
    />
  );

  return (
    <SectionShell header={header} error={error}>
      <div style={{ padding: 'var(--space-3) var(--space-5)' }}>
        <ControlBar>
          <select value={a} onChange={(e) => setA(e.target.value)} aria-label="Baseline harness">
            <option value="">baseline…</option>
            {options}
          </select>
          <select value={b} onChange={(e) => setB(e.target.value)} aria-label="Candidate harness">
            <option value="">candidate…</option>
            {options}
          </select>
          <Button size="sm" icon={<ScaleIcon />} disabled={!a || !b} onClick={() => void run()}>
            Compare
          </Button>
        </ControlBar>
      </div>
      <div style={{ ...bodyScroll, padding: 'var(--space-5)' }}>
        {loading ? <Loading what="harnesses" /> : null}
        {detail ? <Inspector harness={detail} /> : null}
        {!loading && !report ? (
          <EmptyState title="Pick two harnesses to compare">
            A harness is a system prompt, a tool catalog, a model and its sampling settings —
            changing any of them makes a new one.
          </EmptyState>
        ) : null}
        {report ? (
          <>
            <div
              style={{
                ...card,
                borderTopColor: report.comparable ? 'var(--success)' : 'var(--warning)',
                marginBottom: 'var(--space-6)',
              }}
            >
              <div style={heading}>
                {report.comparable ? 'Paired comparison' : 'Not a comparison'}
              </div>
              <div style={{ fontSize: 'var(--fs-body)', marginTop: 'var(--space-2)' }}>
                {report.note}
              </div>
              {report.comparable ? (
                <div style={{ ...mono, marginTop: 'var(--space-3)' }}>
                  baseline {report.pairedSuccess.a}/{report.pairedSuccess.of} · candidate{' '}
                  {report.pairedSuccess.b}/{report.pairedSuccess.of}
                </div>
              ) : null}
            </div>

            <div
              style={{
                display: 'flex',
                gap: 'var(--space-5)',
                marginBottom: 'var(--space-6)',
                flexWrap: 'wrap',
              }}
            >
              <SideCard side={report.a} role="Baseline" />
              <SideCard side={report.b} role="Candidate" />
            </div>

            <GoalList
              title="Regressions — baseline did these, candidate does not"
              tone="var(--danger)"
              goals={report.regressions}
            />
            <GoalList
              title="Fixed — candidate does these, baseline does not"
              tone="var(--success)"
              goals={report.fixes}
            />

            <div style={{ ...heading, marginBottom: 'var(--space-3)' }}>Tool calls per run</div>
            {report.toolDelta.map((row) => (
              <div
                key={row.name}
                style={{
                  display: 'flex',
                  gap: 'var(--space-5)',
                  ...mono,
                  padding: 'var(--space-1) 0',
                }}
              >
                <span style={{ flex: 1, color: 'var(--text-primary)' }}>{row.name}</span>
                <Figure width={60}>{row.a}</Figure>
                <Figure width={60}>{row.b}</Figure>
                <Figure
                  width={70}
                  tone={row.delta > 0 ? 'warn' : row.delta < 0 ? 'accent' : undefined}
                >
                  {row.delta > 0 ? '+' : ''}
                  {row.delta}
                </Figure>
              </div>
            ))}
          </>
        ) : null}
      </div>
    </SectionShell>
  );
}
