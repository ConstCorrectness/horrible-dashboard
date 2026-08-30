/**
 * Trajectories: one pane, three sections — Runs, Datasets, Harness.
 *
 * One pane rather than three, per the pane-consolidation rule: these are three views
 * of one thing (the runs, where they are collected, and what configuration produced
 * them), and three panes would mean three openers and three copies of "which dataset
 * are we looking at".
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
      {section === 'datasets' ? (
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
