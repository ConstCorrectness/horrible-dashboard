/**
 * Trajectories: one pane, four sections — Runs, Live, Datasets, Harness.
 *
 * One pane rather than four, per the pane-consolidation rule: these are four views
 * of one thing (what is running, the runs it becomes, where they are collected, and
 * what configuration produced them), and four panes would mean four openers and four
 * copies of "which dataset are we looking at".
 *
 * Live is socket-fed and has no polling fallback, on purpose: a poll would make "the
 * stream is broken" look identical to "nothing is running".
 *
 * The Runs section is failure-first in the same spirit as the evals results view —
 * nobody opens a trajectory browser to admire the runs that worked. The Harness
 * section is the one that justifies the module: it is where "did my change help" gets
 * an answer, including the answer "these two never ran the same tasks, so this is not
 * a comparison".
 *
 * This file is only the switch. Each section lives in `panels/`, which is what let the
 * whole thing move onto the shared primitives (`PaneHeader`, `DataList`/`DataRow`,
 * `SplitPane`, `Button`, `Chip`, `EmptyState`) and the theme scale. It previously ran
 * to 816 lines over a local `const S` object of raw pixels and legacy alias tokens —
 * and an undefined `var()` silently falls through to its hex fallback, which is how
 * this pane came to render fully dark under the light themes while everything beside
 * it rendered light.
 */
import { useState } from 'react';

import { usePaneSection } from '../../layout/use-sections';
import { DatasetsSection } from './panels/DatasetsSection';
import { HarnessSection } from './panels/HarnessSection';
import { LiveSection } from './panels/LiveSection';
import { CommonsSection } from './panels/CommonsSection';
import { PeersSection } from './panels/PeersSection';
import { RunsSection } from './panels/RunsSection';
import './trajectories.css';

export function TrajectoriesHub() {
  const { section, setSection } = usePaneSection();
  // Set by a run's "Inspect" button so the Harness section opens on that
  // fingerprint. Component state rather than a param: the pane is a singleton, so
  // there is nowhere to hang one, and pretending this is a deep link would be a
  // promise a reload breaks.
  const [inspect, setInspect] = useState<string | undefined>(undefined);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
        background: 'var(--bg-primary)',
        color: 'var(--text-primary)',
        fontSize: 'var(--fs-body)',
      }}
    >
      {section === 'live' ? (
        <LiveSection />
      ) : section === 'peers' ? (
        <PeersSection onPulled={() => setSection('runs')} />
      ) : section === 'commons' ? (
        <CommonsSection />
      ) : section === 'datasets' ? (
        <DatasetsSection />
      ) : section === 'harness' ? (
        <HarnessSection inspect={inspect} />
      ) : (
        <RunsSection
          onInspectHarness={(fingerprint) => {
            setInspect(fingerprint);
            setSection('harness');
          }}
        />
      )}
    </div>
  );
}
