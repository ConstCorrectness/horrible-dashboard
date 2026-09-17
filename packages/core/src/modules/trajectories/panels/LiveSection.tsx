/**
 * Runs happening right now.
 *
 * The section exists because the rest of this pane is a postmortem: a four-minute
 * agent turn appeared, finished, four minutes after it began. Watching an agent work
 * is most of what an observability pane is for, and it was the one thing the module
 * could not do.
 *
 * Entirely socket-fed — there is no polling fallback and deliberately so. A poll
 * would paper over a broken channel and make "the stream is down" indistinguishable
 * from "nothing is running", which is exactly the ambiguity this view exists to
 * remove. When nothing arrives, the empty state says what that means.
 */
import { useEffect, useSyncExternalStore } from 'react';

import { EmptyState, PaneHeader } from '../../../Primitives';
import {
  getTrajectoriesLive,
  initTrajectoriesLive,
  livePeerRuns,
  liveRuns,
  subscribeTrajectoriesLive,
  type LiveStep,
} from '../ws';
import type { PeerRun } from '../api';
import { ago, bodyScroll, card, heading, mono, tokens, usd, SectionShell } from './common';
import { Timeline } from './Timeline';

/** Structural on purpose: a friend's run header is a subset of ours, and every field
 * read here is in both. */
function RunCard({ entry, from }: { entry: { run: PeerRun; steps: LiveStep[] }; from?: string }) {
  const { run, steps } = entry;
  const last = steps[steps.length - 1];
  const tokenLine = tokens(run.tokens_in, run.tokens_out);
  const spend = usd(run.cost_usd);
  return (
    <div style={{ ...card, marginBottom: 'var(--space-4)' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 'var(--space-3)',
          marginBottom: 'var(--space-2)',
        }}
      >
        <span style={{ flex: 1, fontSize: 'var(--fs-lead)', fontWeight: 600 }}>
          {run.goal || '(no goal recorded)'}
        </span>
        {/* A live pulse rather than a static "running" word: the one thing a reader
            wants to know at a glance is whether this is still moving. */}
        <span className="traj-pulse" aria-label="running" />
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-5)', ...mono }}>
        <span>{run.agent_name || run.agent_id || 'agent'}</span>
        <span>{run.model || '—'}</span>
        <span>
          {steps.length} {steps.length === 1 ? 'step' : 'steps'}
        </span>
        <span>started {ago(run.started_at)}</span>
        {/* Only once a provider has reported: a running turn has no totals until its
            first round returns, and `0 tok` would read as a measurement. */}
        {tokenLine ? <span>{tokenLine}</span> : null}
        {spend ? <span>{spend}</span> : null}
        {from ? <span>from {from}</span> : null}
        {last?.name ? <span>latest: {last.name}</span> : null}
      </div>
      <Timeline steps={steps} startedAt={run.started_at} />
    </div>
  );
}

export function LiveSection() {
  useEffect(() => {
    initTrajectoriesLive();
  }, []);

  const state = useSyncExternalStore(subscribeTrajectoriesLive, getTrajectoriesLive);
  const running = liveRuns(state);
  const watching = livePeerRuns(state);

  return (
    <SectionShell
      header={
        <PaneHeader
          title="Live"
          meta={[
            <span key="n" style={mono}>
              {running.length} running
            </span>,
            ...(watching.length
              ? [
                  <span key="w" style={mono}>
                    watching {watching.length}
                  </span>,
                ]
              : []),
          ]}
        />
      }
    >
      <div style={{ ...bodyScroll, padding: 'var(--space-5)' }}>
        {/* A friend's runs, only while this node is in their share session. Shown
            first: they are the reason someone opens Live while sitting in a session. */}
        {watching.length ? (
          <div style={{ marginBottom: 'var(--space-6)' }}>
            <div style={{ ...heading, marginBottom: 'var(--space-3)' }}>Watching</div>
            {watching.map((entry) => (
              <RunCard
                key={`${entry.host}:${entry.run.id}`}
                entry={entry}
                from={entry.hostName}
              />
            ))}
          </div>
        ) : null}
        {running.length === 0 && watching.length ? null : running.length === 0 ? (
          <EmptyState title="Nothing running">
            A run appears here the moment an agent starts acting — but only for
            datasets with capture switched on, which is off by default. Turn it on in
            Datasets.
          </EmptyState>
        ) : (
          <>
            <div style={{ ...heading, marginBottom: 'var(--space-3)' }}>In flight</div>
            {running.map((entry) => (
              <RunCard key={entry.run.id} entry={entry} />
            ))}
          </>
        )}
      </div>
    </SectionShell>
  );
}
