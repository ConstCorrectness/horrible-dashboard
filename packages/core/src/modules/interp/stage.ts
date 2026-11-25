/**
 * Pure model + controller verbs for the Interpretability IDE's open-tabs strip
 * and side-by-side split stage.
 *
 * Stored in `interpretability.suite`'s `pane.params` (`openTabs`, `splitSection`,
 * `splitPrimarySide`) following `ide/openBuffers.ts`:
 * - Survives workspace reloads and tab switches across outer workbench groups.
 * - Preserves every other param on the pane (`turnId`, `modelPath`, etc.) when
 *   dispatching `SET_PANE_PARAMS` (which replaces the params object).
 */
import { openPane, revealSection, setPaneSection } from '../../layout/controller';
import { findPaneAnywhere, listPanes } from '../../layout/model';
import { layoutStore } from '../../layout/store';
import { SUITE_SECTIONS, SUITE_VIEW, type SuiteSection } from './ids';
import { setSubSection } from './SubSections';

const ALL_SECTIONS = new Set<string>(Object.values(SUITE_SECTIONS));

export function isSuiteSection(value: unknown): value is SuiteSection {
  return typeof value === 'string' && ALL_SECTIONS.has(value);
}

/**
 * Natural side-by-side pairings offered as 1-click buttons in `SubjectBar` and
 * `SuiteFrame`, unlocking cross-locus visualizations that were previously hidden
 * behind single-section switching.
 */
export const NATURAL_COMPANIONS: Readonly<
  Partial<Record<SuiteSection, { section: SuiteSection; label: string }>>
> = {
  [SUITE_SECTIONS.stepper]: { section: SUITE_SECTIONS.model, label: 'Model Explorer' },
  [SUITE_SECTIONS.lens]: { section: SUITE_SECTIONS.model, label: 'Model Explorer' },
  [SUITE_SECTIONS.model]: { section: SUITE_SECTIONS.lens, label: 'Logit Lens' },
  [SUITE_SECTIONS.attention]: { section: SUITE_SECTIONS.model, label: 'Model Explorer' },
  [SUITE_SECTIONS.traces]: { section: SUITE_SECTIONS.stepper, label: 'Layer Stepper' },
  [SUITE_SECTIONS.playground]: { section: SUITE_SECTIONS.neurons, label: '3D Neurons' },
  [SUITE_SECTIONS.neurons]: { section: SUITE_SECTIONS.playground, label: 'Playground' },
  [SUITE_SECTIONS.features]: { section: SUITE_SECTIONS.steer, label: 'Feature Steer' },
  [SUITE_SECTIONS.steer]: { section: SUITE_SECTIONS.features, label: 'SAE Features' },
  [SUITE_SECTIONS.evals]: { section: SUITE_SECTIONS.runs, label: 'Runs & Debugger' },
  [SUITE_SECTIONS.context]: { section: SUITE_SECTIONS.runs, label: 'Runs & Debugger' },
  [SUITE_SECTIONS.runs]: { section: SUITE_SECTIONS.context, label: 'Context Window' },
  [SUITE_SECTIONS.runtime]: { section: SUITE_SECTIONS.model, label: 'Model Explorer' },
  [SUITE_SECTIONS.heads]: { section: SUITE_SECTIONS.stepper, label: 'Layer Stepper' },
};

export interface StageState {
  openTabs: SuiteSection[];
  splitSection: SuiteSection | null;
  splitPrimarySide: 'left' | 'right';
}

/** Pure parse of `StageState` from a pane's `params` and `activeSection`. */
export function parseStageState(
  params: Record<string, unknown> | undefined,
  activeSection: SuiteSection = SUITE_SECTIONS.home,
): StageState {
  const rawTabs = Array.isArray(params?.openTabs)
    ? (params.openTabs as unknown[]).filter(isSuiteSection)
    : [];
  const seen = new Set<SuiteSection>();
  const openTabs: SuiteSection[] = [];
  for (const t of rawTabs) {
    if (!seen.has(t)) {
      seen.add(t);
      openTabs.push(t);
    }
  }
  if (!seen.has(activeSection)) {
    seen.add(activeSection);
    openTabs.push(activeSection);
  }
  const rawSplit = params?.splitSection;
  const splitSection = isSuiteSection(rawSplit) && rawSplit !== activeSection ? rawSplit : null;
  if (splitSection && !seen.has(splitSection)) {
    seen.add(splitSection);
    openTabs.push(splitSection);
  }
  const splitPrimarySide = params?.splitPrimarySide === 'right' ? 'right' : 'left';
  return { openTabs, splitSection, splitPrimarySide };
}

/** Pure transition when opening `section` (optionally paired with `splitWith`). */
export function nextOpenParams(
  prevParams: Record<string, unknown> | undefined,
  currentActive: SuiteSection,
  section: SuiteSection,
  opts?: { splitWith?: SuiteSection | null; extraParams?: Record<string, unknown> },
): Record<string, unknown> {
  const cur = parseStageState(prevParams, currentActive);
  const tabs = cur.openTabs.includes(section) ? [...cur.openTabs] : [...cur.openTabs, section];
  let splitSection: SuiteSection | null = cur.splitSection;
  let splitPrimarySide: 'left' | 'right' = cur.splitPrimarySide;

  if (opts && 'splitWith' in opts) {
    splitSection = opts.splitWith && opts.splitWith !== section ? opts.splitWith : null;
    splitPrimarySide = 'left';
  } else if (splitSection === section) {
    // Switching primary onto what was the companion swaps them rather than collapsing.
    splitSection = currentActive !== section ? currentActive : null;
    splitPrimarySide = splitPrimarySide === 'left' ? 'right' : 'left';
  }

  if (splitSection && !tabs.includes(splitSection)) {
    tabs.push(splitSection);
  }

  const next: Record<string, unknown> = {
    ...(prevParams ?? {}),
    ...(opts?.extraParams ?? {}),
    openTabs: tabs,
  };
  if (splitSection) {
    next.splitSection = splitSection;
    next.splitPrimarySide = splitPrimarySide;
  } else {
    delete next.splitSection;
    delete next.splitPrimarySide;
  }
  return next;
}

/** Pure transition when closing `target` from `openTabs`. */
export function nextCloseTabParams(
  prevParams: Record<string, unknown> | undefined,
  currentActive: SuiteSection,
  target: SuiteSection,
): { params: Record<string, unknown>; nextActive: SuiteSection } {
  const cur = parseStageState(prevParams, currentActive);
  if (cur.openTabs.length <= 1) {
    return { params: { ...(prevParams ?? {}), openTabs: cur.openTabs }, nextActive: currentActive };
  }
  const idx = cur.openTabs.indexOf(target);
  const remaining = cur.openTabs.filter((t) => t !== target);
  let nextActive = currentActive;
  let nextSplit = cur.splitSection;

  if (target === currentActive) {
    nextActive = nextSplit ?? remaining[Math.min(idx, remaining.length - 1)] ?? SUITE_SECTIONS.home;
    if (nextSplit === nextActive) nextSplit = null;
  } else if (target === nextSplit) {
    nextSplit = null;
  }

  const next: Record<string, unknown> = { ...(prevParams ?? {}), openTabs: remaining };
  if (nextSplit && nextSplit !== nextActive) {
    next.splitSection = nextSplit;
    next.splitPrimarySide = cur.splitPrimarySide;
  } else {
    delete next.splitSection;
    delete next.splitPrimarySide;
  }
  return { params: next, nextActive };
}

function locateSuite(instanceId?: string) {
  const f = layoutStore.getSnapshot().frame;
  if (instanceId) return findPaneAnywhere(f, instanceId);
  return listPanes(f).find((p) => p.pane.viewId === SUITE_VIEW) ?? null;
}

/**
 * Open the Interpretability IDE at `section`, optionally setting its sub-tab,
 * pairing a companion section side-by-side (`splitWith`), and merging extra params.
 */
export function openSuiteTab(
  section: SuiteSection,
  opts?: {
    splitWith?: SuiteSection | null;
    subTab?: string;
    companionSubTab?: string;
    params?: Record<string, unknown>;
  },
): string | null {
  if (opts?.subTab) setSubSection(section, opts.subTab);
  if (opts?.splitWith && opts.companionSubTab) {
    setSubSection(opts.splitWith, opts.companionSubTab);
  }
  const existing = locateSuite();
  if (!existing) {
    const initial = nextOpenParams(undefined, section, section, {
      splitWith: opts?.splitWith,
      extraParams: opts?.params,
    });
    openPane(SUITE_VIEW, { params: initial });
    return revealSection(section, SUITE_VIEW);
  }
  const currentActive = isSuiteSection(existing.pane.activeSection)
    ? existing.pane.activeSection
    : SUITE_SECTIONS.home;
  const merged = nextOpenParams(existing.pane.params, currentActive, section, {
    splitWith: opts?.splitWith,
    extraParams: opts?.params,
  });
  layoutStore.dispatch({
    type: 'SET_PANE_PARAMS',
    instanceId: existing.pane.instanceId,
    params: merged,
  });
  return revealSection(section, SUITE_VIEW);
}

/** Set or clear the companion split section on an open suite pane. */
export function splitSuiteWith(instanceId: string, companion: SuiteSection | null): void {
  const located = locateSuite(instanceId);
  if (!located) return;
  const active = isSuiteSection(located.pane.activeSection)
    ? located.pane.activeSection
    : SUITE_SECTIONS.home;
  const next = nextOpenParams(located.pane.params, active, active, { splitWith: companion });
  layoutStore.dispatch({
    type: 'SET_PANE_PARAMS',
    instanceId: located.pane.instanceId,
    params: next,
  });
}

/** Swap left and right sides of an active side-by-side split. */
export function swapSuiteSplit(instanceId: string): void {
  const located = locateSuite(instanceId);
  if (!located) return;
  const active = isSuiteSection(located.pane.activeSection)
    ? located.pane.activeSection
    : SUITE_SECTIONS.home;
  const cur = parseStageState(located.pane.params, active);
  if (!cur.splitSection) return;
  layoutStore.dispatch({
    type: 'SET_PANE_PARAMS',
    instanceId: located.pane.instanceId,
    params: {
      ...(located.pane.params ?? {}),
      splitPrimarySide: cur.splitPrimarySide === 'left' ? 'right' : 'left',
    },
  });
}

/** Close one tab from the IDE's stage tab strip. */
export function closeSuiteTab(instanceId: string, target: SuiteSection): void {
  const located = locateSuite(instanceId);
  if (!located) return;
  const active = isSuiteSection(located.pane.activeSection)
    ? located.pane.activeSection
    : SUITE_SECTIONS.home;
  const { params, nextActive } = nextCloseTabParams(located.pane.params, active, target);
  layoutStore.dispatch({
    type: 'SET_PANE_PARAMS',
    instanceId: located.pane.instanceId,
    params,
  });
  if (nextActive !== active) setPaneSection(located.pane.instanceId, nextActive);
}
