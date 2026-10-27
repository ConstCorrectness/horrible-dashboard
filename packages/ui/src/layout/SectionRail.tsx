/**
 * The vertical counterpart of `SectionTabs`: a pane's sections as a rail down its
 * left edge, VS Code's activity bar inside one window.
 *
 * A pane opts in with `sectionsLayout: 'rail'`. It earns its place when a pane has
 * more sections than a top strip can show without scrolling sideways, and when
 * those sections fall into groups worth naming — the interpretability suite's
 * prompt / weights / activations / features / circuits.
 *
 * It is the same machinery as the strip, not a second one: the persisted
 * `activeSection`, the synthesized `section.show:` commands and the agent's
 * section argument all move the one value this renders.
 *
 * Collapsed (icons only) is a per-viewer convenience, so it lives in
 * `localStorage` keyed by view id, not in the layout.
 */
import { useState } from 'react';
import { NAMED_GLYPHS, type SectionDecl } from '@horrible/core';

const COLLAPSE_KEY = 'frame.sectionRail.collapsed:';

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

/** Sections in declaration order, split into runs that share a `group`. */
export function groupSections(sections: SectionDecl[]): { group?: string; items: SectionDecl[] }[] {
  const runs: { group?: string; items: SectionDecl[] }[] = [];
  for (const s of sections) {
    const last = runs[runs.length - 1];
    if (last && last.group === s.group) last.items.push(s);
    else runs.push({ group: s.group, items: [s] });
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
  onPick,
}: {
  viewId: string;
  sections: SectionDecl[];
  active: string | undefined;
  onPick: (id: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed(viewId));
  if (sections.length < 2) return null;

  const toggle = () => {
    setCollapsed((prev) => {
      writeCollapsed(viewId, !prev);
      return !prev;
    });
  };

  return (
    <nav
      className={`frame-section-rail${collapsed ? ' collapsed' : ''}`}
      role="tablist"
      aria-orientation="vertical"
    >
      <div className="frame-section-rail-list">
        {groupSections(sections).map((run, i) => (
          <div key={`${run.group ?? ''}:${i}`} className="frame-section-rail-group">
            {run.group ? (
              <div className="frame-section-rail-heading" title={run.group}>
                {collapsed ? '' : run.group}
              </div>
            ) : null}
            {run.items.map((s) => (
              <button
                key={s.id}
                role="tab"
                aria-selected={s.id === active}
                className={`frame-section-rail-tab${s.id === active ? ' active' : ''}`}
                title={s.key ? `${s.label} (${s.key})` : s.label}
                onClick={() => onPick(s.id)}
              >
                <span className="frame-section-rail-glyph">
                  <SectionGlyph section={s} />
                </span>
                {collapsed ? null : <span className="frame-section-rail-label">{s.label}</span>}
              </button>
            ))}
          </div>
        ))}
      </div>
      <button
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
