/**
 * The vertical counterpart of `SectionTabs`: a pane's sections as a rail down its
 * left edge, VS Code's activity bar inside one window.
 *
 * A pane opts in with `sectionsLayout: 'rail'`. It earns its place when a pane has
 * more sections than a top strip can show without scrolling sideways, and when
 * those sections fall into groups worth naming — the interpretability IDE's
 * progressive workflow groups.
 *
 * It is the same machinery as the strip, not a second one: the persisted
 * `activeSection`, the synthesized `section.show:` commands and the agent's
 * section argument all move the one value this renders.
 *
 * Collapsed (icons only) and per-group folds are per-viewer conveniences, so they
 * live in `localStorage` keyed by view id, not in the layout.
 */
import { useState } from 'react';
import { IconChevron, NAMED_GLYPHS, type SectionDecl } from '@horrible/core';

const COLLAPSE_KEY = 'frame.sectionRail.collapsed:';
const GROUP_COLLAPSE_KEY = 'frame.sectionRail.groupCollapsed:';

function readCollapsed(viewId: string): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY + viewId) === '1';
  } catch {
    return false;
  }
}

function writeCollapsed(viewId: string, collapsed: boolean): void {
  try {
    localStorage.setItem(COLLAPSE_KEY + viewId, collapsed ? '1' : '0');
  } catch {
    /* private window or blocked storage: the rail still works, it just forgets */
  }
}

function readGroupCollapsed(viewId: string, group: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(`${GROUP_COLLAPSE_KEY}${viewId}:${group}`);
    if (raw === '1') return true;
    if (raw === '0') return false;
    return fallback;
  } catch {
    return fallback;
  }
}

function writeGroupCollapsed(viewId: string, group: string, collapsed: boolean): void {
  try {
    localStorage.setItem(`${GROUP_COLLAPSE_KEY}${viewId}:${group}`, collapsed ? '1' : '0');
  } catch {
    /* private window or blocked storage */
  }
}

export interface SectionGroupRun {
  group?: string;
  collapsible: boolean;
  defaultCollapsed: boolean;
  items: SectionDecl[];
}

/** Sections in declaration order, split into runs that share a `group`. */
export function groupSections(sections: SectionDecl[]): SectionGroupRun[] {
  const runs: SectionGroupRun[] = [];
  for (const s of sections) {
    const last = runs[runs.length - 1];
    const collapsible = Boolean(s.group && (s.groupCollapsible || s.groupDefaultCollapsed));
    const defaultCollapsed = Boolean(s.group && s.groupDefaultCollapsed);
    if (last && last.group === s.group) {
      last.items.push(s);
      if (collapsible) last.collapsible = true;
      if (defaultCollapsed) last.defaultCollapsed = true;
    } else {
      runs.push({ group: s.group, collapsible, defaultCollapsed, items: [s] });
    }
  }
  return runs;
}

function SectionGlyph({ section }: { section: SectionDecl }) {
  const Icon = section.glyph ? NAMED_GLYPHS[section.glyph] : undefined;
  if (Icon) return <Icon />;
  return <span aria-hidden>{section.icon ?? section.label.charAt(0)}</span>;
}

export function SectionRail({
  viewId,
  sections,
  active,
  secondaryActive,
  onPick,
}: {
  viewId: string;
  sections: SectionDecl[];
  active: string | undefined;
  /** Companion section shown beside `active` when `allowSectionSplit` is active. */
  secondaryActive?: string;
  onPick: (id: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed(viewId));
  const [groupTick, setGroupTick] = useState(0);
  void groupTick;

  if (sections.length < 2) return null;

  const toggle = () => {
    setCollapsed((prev) => {
      writeCollapsed(viewId, !prev);
      return !prev;
    });
  };

  const toggleGroup = (group: string, currentlyCollapsed: boolean) => {
    writeGroupCollapsed(viewId, group, !currentlyCollapsed);
    setGroupTick((n) => n + 1);
  };

  return (
    <nav
      className={`frame-section-rail${collapsed ? ' collapsed' : ''}`}
      role="tablist"
      aria-orientation="vertical"
    >
      <div className="frame-section-rail-list">
        {groupSections(sections).map((run, i) => {
          const hasActiveItem = run.items.some(
            (s) => s.id === active || (secondaryActive !== undefined && s.id === secondaryActive),
          );
          const storedFolded =
            run.group && run.collapsible
              ? readGroupCollapsed(viewId, run.group, run.defaultCollapsed)
              : false;
          // Never hide the currently active or split-paired tool inside a folded group.
          const groupFolded = Boolean(
            run.group && run.collapsible && storedFolded && !hasActiveItem,
          );

          return (
            <div key={`${run.group ?? ''}:${i}`} className="frame-section-rail-group">
              {run.group ? (
                run.collapsible && !collapsed ? (
                  <button
                    type="button"
                    className="frame-section-rail-group-btn"
                    aria-expanded={!groupFolded}
                    title={
                      hasActiveItem && storedFolded
                        ? `${run.group} (expanded while active)`
                        : groupFolded
                          ? `Expand ${run.group} (${run.items.length} tools)`
                          : `Collapse ${run.group}`
                    }
                    onClick={() => toggleGroup(run.group!, groupFolded)}
                  >
                    <span
                      className="frame-section-rail-group-chevron"
                      style={{ transform: groupFolded ? 'none' : 'rotate(90deg)' }}
                    >
                      <IconChevron />
                    </span>
                    <span className="frame-section-rail-group-text">{run.group}</span>
                    {groupFolded ? (
                      <span className="frame-section-rail-group-count">{run.items.length}</span>
                    ) : null}
                  </button>
                ) : (
                  <div className="frame-section-rail-heading" title={run.group}>
                    {collapsed ? '' : run.group}
                  </div>
                )
              ) : null}
              {!groupFolded &&
                run.items.map((s) => {
                  const isPrimary = s.id === active;
                  const isSplit = !isPrimary && secondaryActive === s.id;
                  return (
                    <button
                      key={s.id}
                      type="button"
                      role="tab"
                      aria-selected={isPrimary}
                      className={`frame-section-rail-tab${isPrimary ? ' active' : ''}${isSplit ? ' split-active' : ''}`}
                      title={
                        isSplit
                          ? `${s.label} (shown side-by-side)`
                          : s.key
                            ? `${s.label} (${s.key})`
                            : s.label
                      }
                      onClick={() => onPick(s.id)}
                    >
                      <span className="frame-section-rail-glyph">
                        <SectionGlyph section={s} />
                      </span>
                      {collapsed ? null : (
                        <span className="frame-section-rail-label">{s.label}</span>
                      )}
                    </button>
                  );
                })}
            </div>
          );
        })}
      </div>
      <button
        type="button"
        className="frame-section-rail-toggle"
        title={collapsed ? 'Show section names' : 'Icons only'}
        aria-label={collapsed ? 'Expand section rail' : 'Collapse section rail'}
        onClick={toggle}
      >
        {collapsed ? '»' : '«'}
      </button>
    </nav>
  );
}
