/**
 * Tabs one level below a rail section.
 *
 * Agentpedia (Runs / Harness / Forks / Duels) and the window's lens lab (Reply /
 * Guess / Compare) were panes with sections of their own, and their components
 * switch on `usePaneSection()`. Inside the suite that hook names the *rail*
 * section, so this host provides `SectionScopeContext` instead: the components
 * are unchanged, and their own buttons (Runs → "fork this round") still move the
 * tab they always moved.
 *
 * The value is module state, not component state — a kept-alive section can be
 * switched by the agent or by another section while it is hidden — and it is
 * remembered per viewer in `localStorage`, the way the rail's collapsed state is.
 */
import { useMemo, useSyncExternalStore, type ReactNode } from 'react';

import { SectionScopeContext, type PaneSections } from '../../layout/use-sections';
import { S } from './styles';

export interface SubTab {
  id: string;
  label: string;
}

const KEY = 'interp.sub:';
const values = new Map<string, string>();
const listeners = new Set<() => void>();

function read(scope: string, fallback: string): string {
  const held = values.get(scope);
  if (held) return held;
  try {
    return localStorage.getItem(KEY + scope) ?? fallback;
  } catch {
    return fallback;
  }
}

/** Switch a sub-section from anywhere (a command, the agent, another section). */
export function setSubSection(scope: string, id: string): void {
  if (values.get(scope) === id) return;
  values.set(scope, id);
  try {
    localStorage.setItem(KEY + scope, id);
  } catch {
    /* blocked storage: the value still holds for this session */
  }
  for (const l of listeners) l();
}

/** The sub-section a scope is showing. */
export function subSectionOf(scope: string, tabs: readonly SubTab[]): string {
  const id = read(scope, tabs[0]?.id ?? '');
  return tabs.some((t) => t.id === id) ? id : (tabs[0]?.id ?? '');
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function SubSections({
  scope,
  tabs,
  children,
}: {
  scope: string;
  tabs: readonly SubTab[];
  children: ReactNode;
}) {
  const section = useSyncExternalStore(subscribe, () => subSectionOf(scope, tabs));
  const value = useMemo<PaneSections>(
    () => ({
      section,
      setSection: (next) => {
        if (tabs.some((t) => t.id === next)) setSubSection(scope, next);
      },
    }),
    [scope, section, tabs],
  );
  return (
    <div style={S.subRoot}>
      <div style={S.subStrip} role="tablist">
        {tabs.map((t, i) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            className="interp-subtab"
            aria-selected={t.id === section}
            style={{ ...S.subTab(t.id === section), ...S.stagger(i) }}
            onClick={() => value.setSection(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <SectionScopeContext.Provider value={value}>
        <div style={S.subBody}>{children}</div>
      </SectionScopeContext.Provider>
    </div>
  );
}
