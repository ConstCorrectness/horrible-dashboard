/**
 * The in-pane tab strip for a view that declares `sections` — the section
 * counterpart of the region strip's tabs, and the chrome that lets several
 * formerly-separate panes live as one.
 *
 * It is host chrome, not module code, on purpose: before this existed every
 * multi-section pane (games, the client drawer, the browser's network view) had
 * hand-rolled its own bar, none of which persisted, and none of which the agent
 * or a keybinding could drive. Rendering it here means one strip, one persisted
 * `activeSection`, one synthesized `section.show:` command per tab.
 */
import type { SectionDecl } from '@horrible/core';

export function SectionTabs({
  sections,
  active,
  onPick,
}: {
  sections: SectionDecl[];
  active: string | undefined;
  onPick: (id: string) => void;
}) {
  if (sections.length < 2) return null;
  return (
    <div className="frame-section-tabs" role="tablist">
      {sections.map((s) => (
        <button
          key={s.id}
          role="tab"
          aria-selected={s.id === active}
          className={`frame-section-tab${s.id === active ? ' active' : ''}`}
          title={s.key ? `${s.label} (${s.key})` : s.label}
          onClick={() => onPick(s.id)}
        >
          {s.icon ? <span aria-hidden>{s.icon}</span> : null}
          <span>{s.label}</span>
        </button>
      ))}
    </div>
  );
}
