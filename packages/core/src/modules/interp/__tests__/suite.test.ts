/**
 * The interpretability IDE's manifest, and the rules that break silently.
 *
 * - The pane id is written out literally in the manifest (a source-scanning test
 *   reads it), so it is pinned to `SUITE_VIEW` here.
 * - Every tool with a `group` belongs to `SUITE_GROUPS`, so `HomeSection` cannot
 *   silently omit an entire group of tools.
 * - The suite keeps sections mounted once opened. A single-key binding that names
 *   only the pane would fire into a hidden section — `x` in the lens deleting a
 *   designer node — so every such binding must name its section too.
 */
import { describe, expect, it } from 'vitest';

import { NAMED_GLYPHS } from '../../../glyphs';
import { evaluateWhen, type KeyContext } from '../../../keymap/context';
import { agentpediaModule } from '../../agentpedia';
import { interpretabilityModule } from '../../interpretability';
import { llamacppModule } from '../../llamacpp';
import { SUITE_SECTIONS, SUITE_VIEW, whenSuiteSection } from '../ids';
import { interpSuiteModule, suiteBodyView } from '../index';
import { SubjectBar } from '../SubjectBar';
import { setSubSection, subSectionOf } from '../SubSections';
import { SUITE_GROUPS, SUITE_TOOLS } from '../tools';

const suite = () => interpSuiteModule.panels!.find((p) => p.id === SUITE_VIEW)!;

function ctx(over: Partial<KeyContext>): KeyContext {
  return {
    paneFocus: null,
    paneInstance: null,
    paneSection: null,
    capture: null,
    captureView: null,
    textInput: false,
    dialogOpen: false,
    fullscreenArea: false,
    keyboardLock: false,
    windowFocused: true,
    shellView: 'desktop',
    platform: 'win',
    host: 'browser',
    ...over,
  };
}

describe('interpretability suite manifest', () => {
  it('declares the IDE pane with rail, keep-alive, SubjectBar, and section splitting', () => {
    expect(suite()).toBeDefined();
    expect(suite().singleton).toBe(true);
    expect(suite().sectionsLayout).toBe('rail');
    expect(suite().sectionsKeepAlive).toBe(true);
    expect(suite().allowSectionSplit).toBe(true);
    expect(suite().stageHeader).toBe(SubjectBar);
  });

  it('draws all 20 sections from the one tool list, Home first and default', () => {
    const sections = suite().sections ?? [];
    expect(sections).toHaveLength(20);
    expect(sections.map((s) => s.id)).toEqual(SUITE_TOOLS.map((t) => t.section));
    expect(sections.filter((s) => s.default).map((s) => s.id)).toEqual([SUITE_SECTIONS.home]);
  });

  it('covers every grouped tool in SUITE_GROUPS so Home never omits a group', () => {
    const allowed = new Set<string>(SUITE_GROUPS);
    for (const tool of SUITE_TOOLS) {
      if (tool.section === SUITE_SECTIONS.home || tool.section === SUITE_SECTIONS.library) {
        expect(tool.group).toBeUndefined();
      } else {
        expect(tool.group && allowed.has(tool.group), tool.section).toBe(true);
      }
    }
    const mech = SUITE_TOOLS.filter((t) => t.group === 'Mechanistic Lab');
    expect(mech.length).toBeGreaterThan(0);
    for (const t of mech) {
      expect(t.groupCollapsible).toBe(true);
      expect(t.groupDefaultCollapsed).toBe(true);
    }
  });

  it('gives every section an embedded body view with a unique title', () => {
    const embedded = new Map(
      (interpSuiteModule.panels ?? []).filter((p) => p.embedded).map((p) => [p.id, p]),
    );
    const titles = new Set<string>();
    for (const section of suite().sections ?? []) {
      expect(section.view).toBe(suiteBodyView(section.id as never));
      const decl = embedded.get(section.view!);
      expect(decl, section.id).toBeDefined();
      expect(titles.has(decl!.title.toLowerCase()), decl!.title).toBe(false);
      titles.add(decl!.title.toLowerCase());
    }
  });

  it('carries the interp agent tools and valid region strips', () => {
    expect((suite().agentTools ?? []).map((t) => t.name).sort()).toEqual([
      'interp.attention',
      'interp.axis',
      'interp.features',
      'interp.goto',
      'interp.graph',
      'interp.heads',
      'interp.lenses',
      'interp.library',
      'interp.nla',
      'interp.state',
      'interp.steer',
    ]);
    expect((suite().agentTools ?? []).filter((t) => t.sideEffect).map((t) => t.name)).toEqual([
      'interp.steer',
    ]);
    const ask = suite().regions?.find((r) => r.id === 'interp.ask');
    expect(ask?.position).toBe('right');
    for (const r of suite().regions ?? []) {
      expect(['t', 'n', 'b']).not.toContain(r.key);
    }
    const view = interpSuiteModule.panels?.find((p) => p.id === 'interp.ask');
    expect(view?.embedded).toBe(true);
  });

  it('validates both section and splitWith in interp.goto', async () => {
    const goto = suite().agentTools!.find((t) => t.name === 'interp.goto')!;
    const badSection = (await goto.handler({ section: 'nowhere' })) as {
      ok: boolean;
      error: string;
    };
    expect(badSection.ok).toBe(false);
    expect(badSection.error).toContain('attention');

    const badSplit = (await goto.handler({ section: 'stepper', splitWith: 'nowhere' })) as {
      ok: boolean;
      error: string;
    };
    expect(badSplit.ok).toBe(false);
    expect(badSplit.error).toContain('splitWith');
  });

  it('names a glyph that exists for every rail entry', () => {
    for (const section of suite().sections ?? []) {
      expect(NAMED_GLYPHS[section.glyph ?? ''], section.id).toBeDefined();
    }
  });
});

describe('keybindings into kept-alive sections', () => {
  const bindings = [
    ...(interpretabilityModule.keybindings ?? []),
    ...(llamacppModule.keybindings ?? []),
    ...(agentpediaModule.keybindings ?? []),
  ].filter((b) => b.when?.includes(SUITE_VIEW));

  it('every suite-scoped binding also names its section', () => {
    expect(bindings.length).toBeGreaterThan(0);
    for (const b of bindings) expect(b.when, b.command).toContain('paneSection ==');
  });

  it('fires only while its own section is on screen', () => {
    const design = whenSuiteSection(SUITE_SECTIONS.design);
    expect(evaluateWhen(design, ctx({ paneFocus: SUITE_VIEW, paneSection: 'design' }))).toBe(true);
    expect(evaluateWhen(design, ctx({ paneFocus: SUITE_VIEW, paneSection: 'lens' }))).toBe(false);
    expect(evaluateWhen(design, ctx({ paneFocus: 'editor.buffer', paneSection: 'design' }))).toBe(
      false,
    );
  });
});

describe('sub-sections', () => {
  const tabs = [
    { id: 'a', label: 'A' },
    { id: 'b', label: 'B' },
  ];

  it('falls back to the first tab, and to it again for an id it does not list', () => {
    expect(subSectionOf('test-scope-1', tabs)).toBe('a');
    setSubSection('test-scope-1', 'gone');
    expect(subSectionOf('test-scope-1', tabs)).toBe('a');
  });

  it('holds a switch made while nothing is mounted', () => {
    setSubSection('test-scope-2', 'b');
    expect(subSectionOf('test-scope-2', tabs)).toBe('b');
  });
});
