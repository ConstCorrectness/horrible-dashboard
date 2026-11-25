/**
 * Unified Agent Runs & Trajectories workspace (`SUITE_SECTIONS.runs`).
 *
 * Composes `TrajectoriesHub` (fleet index, live runs, wire requests, paired
 * harness stats, lineage tree, datasets, shared runs, OTLP) and `AgentpediaHub`
 * (4-column `Shown · Wire · Did · Cost` round debugger, forks, duels) through
 * `SectionScopeContext`, with one import per sibling module instead of reaching
 * into `trajectories/panels/*`.
 */
import { useMemo, useState } from 'react';

import {
  SectionScopeContext,
  usePaneSection,
  type PaneSections,
} from '../../../layout/use-sections';
import { AgentpediaHub } from '../../agentpedia/AgentpediaHub';
import { TrajectoriesHub } from '../../trajectories/TrajectoriesHub';
import { SUITE_SECTIONS } from '../ids';
import { S } from '../styles';
import { SubSections, type SubTab } from '../SubSections';

export const RUNS_WORKSPACE_TABS: readonly SubTab[] = [
  { id: 'all', label: 'All Runs' },
  { id: 'runs', label: 'Turn Debugger' },
  { id: 'live', label: 'Live' },
  { id: 'follow', label: 'Wire Requests' },
  { id: 'harness', label: 'Harnesses & Lineage' },
  { id: 'forks', label: 'Forks' },
  { id: 'duels', label: 'Duels' },
  { id: 'datasets', label: 'Datasets & OTLP' },
];

function SubModeBar<T extends string>({
  options,
  value,
  onChange,
}: {
  options: readonly { id: T; label: string }[];
  value: T;
  onChange: (next: T) => void;
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '6px 12px',
        borderBottom: '1px solid var(--border)',
        background: 'var(--bg-secondary)',
        flex: 'none',
      }}
    >
      {options.map((opt) => {
        const on = opt.id === value;
        return (
          <button
            key={opt.id}
            type="button"
            className="interp-mini-action"
            aria-pressed={on}
            style={{
              height: 22,
              padding: '0 9px',
              border: `1px solid ${on ? 'var(--accent)' : 'var(--border)'}`,
              borderRadius: 3,
              background: on
                ? 'color-mix(in srgb, var(--accent) 14%, transparent)'
                : 'var(--bg-primary)',
              color: on ? 'var(--text-primary)' : 'var(--text-secondary)',
              fontSize: 11,
              fontWeight: on ? 700 : 500,
              cursor: 'pointer',
            }}
            onClick={() => onChange(opt.id)}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function RunsWorkspaceInner() {
  const { section, setSection } = usePaneSection();
  const [harnessView, setHarnessView] = useState<'harness' | 'lineage'>('harness');
  const [dataView, setDataView] = useState<'datasets' | 'shared' | 'connect'>('datasets');

  const trajSection =
    section === 'harness'
      ? harnessView
      : section === 'datasets'
        ? dataView
        : section === 'all'
          ? 'runs'
          : section;

  const trajScope = useMemo<PaneSections>(
    () => ({
      section: trajSection,
      setSection: (next: string) => {
        if (next === 'runs') setSection('all');
        else if (next === 'harness' || next === 'lineage') {
          setHarnessView(next);
          setSection('harness');
        } else if (next === 'datasets' || next === 'shared' || next === 'connect') {
          setDataView(next);
          setSection('datasets');
        } else {
          setSection(next);
        }
      },
    }),
    [setSection, trajSection],
  );

  if (section === 'runs' || section === 'forks' || section === 'duels') {
    return <AgentpediaHub />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {section === 'harness' ? (
        <SubModeBar
          value={harnessView}
          onChange={setHarnessView}
          options={[
            { id: 'harness', label: 'Harness Compare (Paired A/B)' },
            { id: 'lineage', label: 'Revision Lineage Tree' },
          ]}
        />
      ) : section === 'datasets' ? (
        <SubModeBar
          value={dataView}
          onChange={setDataView}
          options={[
            { id: 'datasets', label: 'Capture & SFT Export' },
            { id: 'shared', label: 'Shared & Commons' },
            { id: 'connect', label: 'OTLP Receiver & SDK' },
          ]}
        />
      ) : null}
      <SectionScopeContext.Provider value={trajScope}>
        <div style={S.subBody}>
          <TrajectoriesHub />
        </div>
      </SectionScopeContext.Provider>
    </div>
  );
}

export function RunsWorkspace() {
  return (
    <SubSections scope={SUITE_SECTIONS.runs} tabs={RUNS_WORKSPACE_TABS}>
      <RunsWorkspaceInner />
    </SubSections>
  );
}
