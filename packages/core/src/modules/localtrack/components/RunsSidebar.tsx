/**
 * The run list: which runs are overlaid on the charts.
 *
 * Three things this used to get wrong, all now fixed here:
 *
 * - **The visibility control was an emoji** (`👁` / `👁‍🗨`) on a `<button>`. Two
 *   problems, not one: it breaks the house rule against native emoji as icons
 *   (a third-party font's opinion about colour and size), and it was not a
 *   checkbox, so nothing announced it as one and the space bar did nothing. It is
 *   a real `<input type="checkbox">` now, visually hidden behind the run's own
 *   colour swatch — which is more useful anyway, because that swatch is the same
 *   colour as the run's line in every chart.
 * - **The "first 5" cap was silent.** A project with 200 runs showed 200 rows with
 *   five ticked and no explanation. The count line says so now.
 * - **A hand-rolled fixed-position modal** for "new project", complete with its own
 *   backdrop and z-index. `dialogs.prompt` already exists, is themed, is focus-
 *   managed and stacks properly with everything else — 80 lines deleted.
 *
 * Deletion is wired for the first time: `removeRun` and `removeProject` existed in
 * the store, were exported by the hook, and were attached to no control at all.
 */
import { useState } from 'react';

import { Button, Chip } from '../../../Primitives';
import { dialogs } from '../../../dialogs';
import { DEFAULT_SELECTION, getRunColor, useLocalTrackStore } from '../store';
import type { Run, RunStatus } from '../types';
import { LocalTrackIcon } from './LocalTrackIcon';

/** Vector glyphs. `currentColor` throughout — the container decides the colour. */
const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

function IconRefresh() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" {...stroke} aria-hidden>
      <path d="M10 6a4 4 0 1 1-1.2-2.8" />
      <path d="M10.5 1.5V4H8" />
    </svg>
  );
}

function IconCollapse() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" {...stroke} aria-hidden>
      <path d="M7.5 2.5 4 6l3.5 3.5" />
    </svg>
  );
}

function IconTrash() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" {...stroke} aria-hidden>
      <path d="M2.5 3.5h7M5 3.5V2.5h2v1M3.5 3.5l.5 6h4l.5-6" />
    </svg>
  );
}

/**
 * A run's status as a verdict.
 *
 * `running` is `info`, never `ok`: a run that has not finished has not succeeded,
 * and drawing it green is how a sweep at step 3 of 5000 comes to look done.
 */
function statusKind(status: RunStatus) {
  if (status === 'running') return 'info' as const;
  if (status === 'finished') return 'ok' as const;
  return 'fail' as const;
}

export function RunsSidebar({
  collapsed,
  onToggleCollapse,
}: {
  collapsed: boolean;
  onToggleCollapse: () => void;
}) {
  const {
    projects,
    activeProjectId,
    setActiveProject,
    createNewProject,
    removeProject,
    runs,
    selectedRunIds,
    toggleRunSelection,
    selectAllRuns,
    deselectAllRuns,
    removeRun,
    openRunDetails,
    loadRuns,
  } = useLocalTrackStore();

  const [search, setSearch] = useState('');

  const needle = search.trim().toLowerCase();
  const filteredRuns = needle
    ? runs.filter(
        (r) =>
          r.name.toLowerCase().includes(needle) ||
          r.tags.some((t) => t.toLowerCase().includes(needle)),
      )
    : runs;

  const newProject = async () => {
    const name = await dialogs.prompt({
      title: 'New project',
      placeholder: 'llama3-lora-sft',
      confirmLabel: 'Create',
    });
    if (name?.trim()) await createNewProject(name.trim());
  };

  const deleteProject = async () => {
    const project = projects.find((p) => p.id === activeProjectId);
    if (!project) return;
    const ok = await dialogs.confirm({
      title: `Delete “${project.name}”?`,
      // Name the blast radius. A project delete cascades to every run and every
      // metric point under it, which the button alone does not convey.
      message: `Its ${project.run_count} run${project.run_count === 1 ? '' : 's'} and all their metrics are deleted too. This cannot be undone.`,
      confirmLabel: 'Delete project',
      danger: true,
    });
    if (ok) await removeProject(project.id);
  };

  const deleteRun = async (run: Run) => {
    const ok = await dialogs.confirm({
      title: `Delete “${run.name}”?`,
      message: 'Its metrics and artifacts go with it. This cannot be undone.',
      confirmLabel: 'Delete run',
      danger: true,
    });
    if (ok) await removeRun(run.id);
  };

  if (collapsed) {
    return (
      <div
        style={{
          width: 44,
          borderRight: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: 'var(--space-5) var(--space-2)',
          gap: 'var(--space-5)',
          background: 'var(--bg-raised)',
        }}
      >
        <Button intent="ghost" size="sm" onClick={onToggleCollapse} title="Expand runs">
          <LocalTrackIcon size={16} />
        </Button>
        <div
          style={{
            writingMode: 'vertical-lr',
            fontSize: 'var(--fs-meta)',
            fontFamily: 'var(--font-mono)',
            color: 'var(--text-dim)',
            letterSpacing: 'var(--tracking-badge)',
          }}
        >
          RUNS ({runs.length})
        </div>
      </div>
    );
  }

  const activeProject = projects.find((p) => p.id === activeProjectId);
  // The cap is only worth explaining while it is actually hiding something.
  const capApplies = runs.length > DEFAULT_SELECTION && selectedRunIds.size === DEFAULT_SELECTION;

  return (
    <aside
      style={{
        width: 280,
        minWidth: 280,
        borderRight: '1px solid var(--border)',
        background: 'var(--bg-raised)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        userSelect: 'none',
      }}
    >
      <div
        style={{
          padding: 'var(--space-5)',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-4)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
          <LocalTrackIcon size={16} />
          <span
            style={{
              flex: 1,
              fontSize: 'var(--fs-label)',
              fontWeight: 'var(--fw-bold)',
              textTransform: 'uppercase',
              letterSpacing: 'var(--tracking-display)',
              color: 'var(--text-dim)',
            }}
          >
            Project
          </span>
          <Button size="sm" onClick={newProject}>
            New
          </Button>
          <Button
            intent="ghost"
            size="sm"
            onClick={onToggleCollapse}
            title="Collapse"
            icon={<IconCollapse />}
          />
        </div>

        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
          <select
            value={activeProjectId}
            onChange={(e) => setActiveProject(e.target.value)}
            style={{
              flex: 1,
              minWidth: 0,
              background: 'var(--bg-inset)',
              color: 'var(--text)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              // Horizontal only: `controls.css` fixes a select's height and strips
              // its vertical padding (the One Height Rule). Adding it back squeezes
              // the content box under the text, and a native select — which also
              // reserves room for its arrow — clips along the bottom.
              padding: '0 var(--space-3)',
              fontSize: 'var(--fs-body)',
            }}
          >
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.run_count})
              </option>
            ))}
          </select>
          <Button
            intent="danger"
            size="sm"
            onClick={deleteProject}
            disabled={!activeProject}
            title="Delete this project"
            icon={<IconTrash />}
          />
        </div>
      </div>

      <div
        style={{
          padding: 'var(--space-4) var(--space-5)',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-3)',
        }}
      >
        <input
          type="search"
          placeholder="Filter by name or tag"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{
            background: 'var(--bg-inset)',
            color: 'var(--text)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            // Horizontal padding only: `controls.css` fixes this control's height and
            // strips its vertical padding (the One Height Rule) — see theming.mdx.
            padding: '0 var(--space-3)',
            fontSize: 'var(--fs-meta)',
          }}
        />

        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 'var(--space-2)',
          }}
        >
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 'var(--fs-meta)',
              color: 'var(--text-dim)',
            }}
          >
            {selectedRunIds.size} / {runs.length} charted
          </span>
          <div style={{ display: 'flex', gap: 'var(--space-1)' }}>
            <Button intent="ghost" size="sm" onClick={selectAllRuns}>
              All
            </Button>
            <Button intent="ghost" size="sm" onClick={deselectAllRuns}>
              None
            </Button>
            <Button
              intent="ghost"
              size="sm"
              onClick={() => loadRuns()}
              title="Refresh"
              icon={<IconRefresh />}
            />
          </div>
        </div>

        {capApplies && (
          <span style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-faint)' }}>
            Showing the newest {DEFAULT_SELECTION} by default — tick any run to add it.
          </span>
        )}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-2) 0' }}>
        {filteredRuns.length === 0 ? (
          <div
            style={{
              padding: 'var(--space-7) var(--space-5)',
              textAlign: 'center',
              color: 'var(--text-dim)',
              fontSize: 'var(--fs-meta)',
            }}
          >
            {runs.length === 0
              ? 'No runs yet. Start a training run or an eval sweep and they appear here.'
              : 'No run matches that filter.'}
          </div>
        ) : (
          filteredRuns.map((run, idx) => {
            const isSelected = selectedRunIds.has(run.id);
            const color = getRunColor(run.id, idx);

            return (
              <div
                key={run.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  padding: 'var(--space-3) var(--space-5)',
                  gap: 'var(--space-3)',
                  fontSize: 'var(--fs-meta)',
                  background: isSelected ? 'var(--accent-dim)' : 'transparent',
                  borderLeft: `2px solid ${isSelected ? color : 'transparent'}`,
                }}
              >
                {/* The real control. Hidden but present, so it keeps the keyboard,
                    the focus ring and the announcement; the swatch beside it is
                    what you actually see, tinted with the run's own line colour. */}
                <label
                  title={isSelected ? 'Hide from charts' : 'Show in charts'}
                  style={{
                    display: 'grid',
                    placeItems: 'center',
                    width: 14,
                    height: 14,
                    flex: 'none',
                    cursor: 'pointer',
                    borderRadius: 3,
                    border: `1px solid ${isSelected ? color : 'var(--border-strong)'}`,
                    background: isSelected ? color : 'transparent',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleRunSelection(run.id)}
                    /* The wrapping label has no text (it draws a swatch), and a
                       `title` on a label does not become the input's accessible
                       name — so without this the control announces as an unnamed
                       checkbox. Names the run, since a screen reader hears these
                       one after another. */
                    aria-label={`Chart ${run.name}`}
                    style={{
                      position: 'absolute',
                      width: 1,
                      height: 1,
                      opacity: 0,
                      margin: 0,
                    }}
                  />
                </label>

                <button
                  type="button"
                  onClick={() => openRunDetails(run)}
                  style={{
                    flex: 1,
                    minWidth: 0,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'flex-start',
                    gap: 'var(--space-1)',
                    background: 'none',
                    border: 'none',
                    padding: 0,
                    cursor: 'pointer',
                    textAlign: 'left',
                  }}
                >
                  <span
                    style={{
                      maxWidth: '100%',
                      fontWeight: 'var(--fw-medium)',
                      fontSize: 'var(--fs-body)',
                      color: 'var(--text)',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {run.name}
                  </span>
                  <span
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 'var(--space-2)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: 'var(--fs-micro)',
                      color: 'var(--text-dim)',
                    }}
                  >
                    <Chip kind={statusKind(run.status)} dot>
                      {run.status}
                    </Chip>
                    {run.duration_seconds > 0 && <span>{Math.round(run.duration_seconds)}s</span>}
                  </span>
                </button>

                <Button
                  intent="ghost"
                  size="sm"
                  onClick={() => deleteRun(run)}
                  title={`Delete ${run.name}`}
                  icon={<IconTrash />}
                />
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
