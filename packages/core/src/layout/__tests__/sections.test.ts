/**
 * The two consolidation primitives: `embedded` (a view that lives only inside
 * another pane) and `sections` (in-pane tabs).
 *
 * The invariant these guard is the one that makes merging panes safe at all:
 * **merging a pane must never reduce agent reachability**. An embedded view
 * disappears from every human-facing opener AND from `list_available_panes` as a
 * top-level row — but stays resolvable by name, and stays listed under its host.
 *
 * Views are registered synthetically rather than imported from real modules — a
 * manifest that reaches the editor pulls in a WebSocket at import time and dies
 * without jsdom (see controller.test.ts).
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import {
  activeSectionOf,
  defaultSectionFor,
  dockSidesOf,
  isDockable,
  openPane,
  revealSection,
  sectionsOf,
  setPaneSection,
  showTarget,
  VIEW_ALIASES,
} from '../controller';
import { findPaneAnywhere } from '../model';
import { railEntries } from '../rail';
import { resolveShowTarget } from '../show';
import { deserialize, serialize } from '../serialize';
import { layoutStore } from '../store';

const Stub = () => null;
const SectionBody = () => null;

beforeAll(() => {
  registry.register({
    id: 'sections-test',
    title: 'Sections test',
    panels: [
      {
        id: 's.host',
        title: 'Host Pane',
        component: Stub,
        role: 'tool',
        defaultDock: 'right',
        singleton: true,
        sections: [
          { id: 'play', label: 'Play', key: 'p' },
          { id: 'friends', label: 'Friends', component: SectionBody, default: true },
          { id: 'requests', label: 'Requests', view: 's.embedded' },
        ],
      },
      // Embedded: a real registered view, but only ever rendered inside a host.
      {
        id: 's.embedded',
        title: 'Embedded Body',
        component: Stub,
        role: 'tool',
        defaultDock: 'left',
        embedded: true,
        singleton: true,
      },
      { id: 's.plain', title: 'Plain Tool', component: Stub, role: 'tool', singleton: true },
      // A declaration mistake: embedded, but nothing hosts it.
      {
        id: 's.stranded',
        title: 'Stranded Body',
        component: Stub,
        role: 'tool',
        embedded: true,
        singleton: true,
      },
    ],
  });
});

beforeEach(() => {
  layoutStore.resetForTests();
});

describe('embedded views', () => {
  it('get no opener command, but a normal view still does', () => {
    const ids = registry.commands.map((c) => c.id);
    expect(ids).toContain('pane.open:s.plain');
    expect(ids).toContain('pane.open:s.host');
    expect(ids).not.toContain('pane.open:s.embedded');
  });

  it('are never dockable, so they earn no rail glyph', () => {
    expect(isDockable('s.plain')).toBe(true);
    expect(dockSidesOf('s.embedded')).toEqual([]);
    expect(isDockable('s.embedded')).toBe(false);

    const frame = layoutStore.getSnapshot().frame;
    const glyphs = railEntries(frame, 'left').map((e) => e.viewId);
    expect(glyphs).toContain('s.plain');
    expect(glyphs).not.toContain('s.embedded');
  });

  it('stay reachable by name, revealed inside their host', () => {
    // The whole point: it vanishes from the openers, not from the vocabulary —
    // and "show it" means "show it where it lives", never a standalone copy.
    const byTitle = showTarget('Embedded Body');
    expect(byTitle.ok).toBe(true);
    expect(byTitle.viewId).toBe('s.embedded');
    expect(byTitle.action).toBe('revealed');
    // It is the section body of s.host:requests, so that is what came forward.
    expect(byTitle.section).toBe('requests');
    expect(byTitle.instanceId).toBeTruthy();
    const pane = findPaneAnywhere(layoutStore.getSnapshot().frame, byTitle.instanceId!)!.pane;
    expect(pane.viewId).toBe('s.host');
  });

  it('fail loudly when nothing hosts them, rather than opening anyway', () => {
    // `embedded` with no region and no section is a declaration error. Quietly
    // opening it standalone would be the flag lying; `ok: false` is what makes
    // the mistake visible instead of shipping a pane that says it doesn't exist.
    const res = showTarget('Stranded Body');
    expect(res.ok).toBe(false);
    expect(res.instanceId).toBeUndefined();
  });

  it('are not a top-level row for the agent, but are listed under their host', async () => {
    const { executeTool } = await import('../../modules/agent/tool-exec');
    const res = (await executeTool('list_available_panes', {})) as {
      views: Array<{ id: string; sections?: Array<{ id: string }> }>;
    };
    const ids = res.views.map((v) => v.id);
    expect(ids).toContain('s.host');
    expect(ids).toContain('s.plain');
    expect(ids).not.toContain('s.embedded');
    const host = res.views.find((v) => v.id === 's.host')!;
    expect(host.sections?.map((s) => s.id)).toEqual(['play', 'friends', 'requests']);
  });
});

describe('retired panes stay reachable (VIEW_ALIASES)', () => {
  // The governing invariant of the whole consolidation: **merging a pane must
  // never reduce agent reachability**. A workspace layout is disposable and
  // reseeds from its preset, but a name the user — or the agent — has ever used
  // should never stop resolving. This is the table of what was merged away.
  const RETIRED: Array<[string, string, string]> = [
    ['games.ladder', 'games.lobby', 'career'],
    ['Ladder', 'games.lobby', 'career'],
    ['games.challenges', 'games.lobby', 'career'],
    ['Challenges', 'games.lobby', 'career'],
    ['games.profile', 'games.lobby', 'career'],
    ['Profile', 'games.lobby', 'career'],
    ['games.replays', 'games.lobby', 'replays'],
    ['games.players', 'games.lobby', 'social'],
    ['Players', 'games.lobby', 'social'],
    ['games.plaza', 'games.lobby', 'social'],
    ['The Plaza', 'games.lobby', 'social'],
    ['social.friends', 'people.home', 'friends'],
    ['Friends', 'people.home', 'friends'],
    ['network.peers', 'people.home', 'friends'],
    ['Peers', 'people.home', 'friends'],
    ['network.lobby', 'people.home', 'friends'],
    ['network.chat', 'people.home', 'messages'],
    ['Peer Chat', 'people.home', 'messages'],
    ['commons.directory', 'people.home', 'discover'],
    ['Commons', 'people.home', 'discover'],
    ['commons.requests', 'people.home', 'requests'],
    ['commons.profile', 'people.home', 'me'],
    ['network.monitor', 'people.home', 'me'],
    ['Peer Monitor', 'people.home', 'me'],
    ['network.relay', 'people.home', 'me'],
    ['Agent Relay', 'people.home', 'me'],
    ['files.tree', 'explorer.home', 'files'],
    ['notebook.browser', 'explorer.home', 'notebooks'],
    ['Notebooks', 'explorer.home', 'notebooks'],
    ['training.projects', 'explorer.home', 'projects'],
    ['Training Projects', 'explorer.home', 'projects'],
  ];

  /**
   * The two that left Explorer again for a region strip on their own document.
   * Listed separately because they resolve to a different *shape* of target, and
   * flattening them into the table above would have meant asserting nothing about
   * which kind came back.
   */
  const RETIRED_TO_REGION: Array<[string, string]> = [
    ['flow.library', 'flow.library'],
    ['Flows', 'flow.library'],
    ['records.list', 'records.list'],
    ['Tables', 'records.list'],
  ];

  const candidates = () => ({
    views: [
      {
        id: 'games.lobby',
        title: 'Games',
        sections: [
          { id: 'career', label: 'Career' },
          { id: 'replays', label: 'Replays' },
          { id: 'social', label: 'Social' },
        ],
      },
      {
        id: 'people.home',
        title: 'People',
        sections: [
          { id: 'friends', label: 'Friends' },
          { id: 'messages', label: 'Messages' },
          { id: 'discover', label: 'Discover' },
          { id: 'requests', label: 'Requests' },
          { id: 'me', label: 'Me' },
        ],
      },
      {
        id: 'explorer.home',
        title: 'Explorer',
        sections: [
          { id: 'files', label: 'Files' },
          { id: 'notebooks', label: 'Notebooks' },
          { id: 'projects', label: 'Projects' },
        ],
      },
    ],
    workspaces: [],
    aliases: VIEW_ALIASES,
  });

  it.each(RETIRED)('%s resolves to %s / %s', (name, viewId, section) => {
    expect(resolveShowTarget(name, candidates())).toEqual({ kind: 'view', viewId, section });
  });

  it.each(RETIRED_TO_REGION)('%s resolves to the %s region strip', (name, regionViewId) => {
    expect(resolveShowTarget(name, candidates())).toEqual({ kind: 'region', regionViewId });
  });

  it('every alias points at a section the target view actually declares', () => {
    // An alias naming a section that was renamed away would resolve and then land
    // the pane on its default tab, silently showing the wrong thing.
    for (const [name, target] of Object.entries(VIEW_ALIASES)) {
      if (target.kind !== 'view' || !target.section) continue;
      const declared = sectionsOf(target.viewId).map((s) => s.id);
      // Only assert for views this test file registers; the real modules are not
      // importable here (a manifest that reaches the editor needs jsdom).
      if (declared.length === 0) continue;
      expect(declared, `alias ${name}`).toContain(target.section);
    }
  });
});

describe('contributed explorer sources', () => {
  // The point of the mechanism: a module hands a browser to a pane it does not
  // own. Registered *after* the host below, because module order must not matter
  // — the merge happens at read time, not at registration.
  beforeAll(() => {
    registry.register({
      id: 'x-host-test',
      title: 'Explorer host test',
      panels: [
        {
          id: 'x.home',
          title: 'X Explorer',
          component: Stub,
          role: 'tool',
          singleton: true,
          explorerHost: true,
          sections: [{ id: 'own', label: 'Own', component: SectionBody, default: true }],
        },
      ],
    });
    registry.register({
      id: 'x-source-test',
      title: 'Explorer source test',
      explorerSources: [{ id: 'lent', label: 'Lent', component: SectionBody, key: 'l' }],
    });
  });

  it('appends after the host’s own sections, whatever the registration order', () => {
    expect(sectionsOf('x.home').map((s) => s.id)).toEqual(['own', 'lent']);
    // The host keeps its own default; a contribution never steals it.
    expect(defaultSectionFor('x.home')).toBe('own');
  });

  it('is indistinguishable from a declared section downstream', () => {
    const ids = registry.commands.map((c) => c.id);
    expect(ids).toContain('section.show:x.home:lent');
    expect(registry.keybindings).toContainEqual(
      expect.objectContaining({ key: 'l', command: 'section.show:x.home:lent', scope: 'x.home' }),
    );
    const res = showTarget('Lent');
    expect(res.ok).toBe(true);
    expect(res.viewId).toBe('x.home');
    expect(res.section).toBe('lent');
  });

  it('keeps decl identity stable between registrations', () => {
    // These getters run on every render path. A fresh object each call would
    // defeat `useSyncExternalStore` snapshots and every memo keyed on the decl.
    const first = registry.panels.find((v) => v.id === 'x.home');
    expect(registry.panels.find((v) => v.id === 'x.home')).toBe(first);
    // A view with no contributions is passed through untouched, not copied.
    const plain = registry.panels.find((v) => v.id === 's.plain');
    expect(registry.panels.find((v) => v.id === 's.plain')).toBe(plain);
  });
});

describe('sections', () => {
  it('reports declarations and picks the declared default', () => {
    expect(sectionsOf('s.host').map((s) => s.id)).toEqual(['play', 'friends', 'requests']);
    // `friends` is marked default even though `play` is declared first.
    expect(defaultSectionFor('s.host')).toBe('friends');
    expect(defaultSectionFor('s.plain')).toBeUndefined();
  });

  it('synthesizes a command per section and a host-scoped pick key', () => {
    const ids = registry.commands.map((c) => c.id);
    expect(ids).toContain('section.show:s.host:play');
    expect(ids).toContain('section.show:s.host:requests');

    const binding = registry.keybindings.find((k) => k.command === 'section.show:s.host:play');
    expect(binding).toMatchObject({ key: 'p', scope: 's.host' });
    // Sections with no declared key get no binding — not a random one.
    expect(registry.keybindings.some((k) => k.command === 'section.show:s.host:friends')).toBe(
      false,
    );
  });

  it('switches per instance and refuses a section the view does not declare', () => {
    const instanceId = openPane('s.host')!;
    expect(setPaneSection(instanceId, 'play')).toBe(true);
    const pane = findPaneAnywhere(layoutStore.getSnapshot().frame, instanceId)!.pane;
    expect(activeSectionOf(pane)).toBe('play');

    expect(setPaneSection(instanceId, 'nope')).toBe(false);
    expect(setPaneSection('no.such#1', 'play')).toBe(false);
  });

  it('show() resolves a section label and lands the pane on it', () => {
    const res = showTarget('Requests');
    expect(res.ok).toBe(true);
    expect(res.viewId).toBe('s.host');
    expect(res.section).toBe('requests');
  });

  it('reports the active section and its siblings even with no provider', async () => {
    // Many sections have no `useAgentContext` — only the mounted body can provide
    // at all. A bare `{section}` left the model with nothing to answer from, so it
    // answered from whatever *else* was in its context: confidently, and wrongly.
    // `hasPayload: false` says so out loud, because an empty snapshot reads as
    // silence rather than as "nothing to report".
    const { readPaneAgentContext } = await import('../controller');
    const instanceId = openPane('s.host')!;
    setPaneSection(instanceId, 'requests');
    expect(readPaneAgentContext(instanceId)).toEqual({
      section: 'requests',
      sections: ['play', 'friends', 'requests'],
      hasPayload: false,
    });
  });

  it('show() on the bare pane leaves it on its default section', () => {
    const res = showTarget('s.host');
    expect(res.section).toBe('friends');
  });

  it('revealSection opens the host when it is not open yet', () => {
    expect(findPaneAnywhere(layoutStore.getSnapshot().frame, 's.host#1')).toBeNull();
    const instanceId = revealSection('play');
    expect(instanceId).toBeTruthy();
    const pane = findPaneAnywhere(layoutStore.getSnapshot().frame, instanceId!)!.pane;
    expect(pane.viewId).toBe('s.host');
    expect(activeSectionOf(pane)).toBe('play');
  });

  it('drops an embedded view from a restored dock, but keeps it in the centre', () => {
    // A layout saved before the view was embedded. Docking it again would rebuild
    // exactly the competing home embedding removes — and `dockSidesOf` says that
    // state is impossible, so restoring it is restoring something today's code
    // cannot produce. A centre placement is a deliberate act and survives.
    const docked = openPane('s.plain')!;
    const centred = openPane('s.host')!;
    const blob = serialize(layoutStore.getSnapshot().frame);
    const known = new Set([...registry.panels, ...registry.widgets].map((v) => v.id));

    const kept = deserialize(blob, known)!;
    expect(findPaneAnywhere(kept, docked)).not.toBeNull();

    const pruned = deserialize(blob, known, new Set(['s.plain']))!;
    expect(pruned.docks.left.tools.map((t) => t.viewId)).not.toContain('s.plain');
    expect(findPaneAnywhere(pruned, centred)).not.toBeNull();
  });

  it('survives a serialize → deserialize round trip', () => {
    const instanceId = openPane('s.host')!;
    setPaneSection(instanceId, 'requests');
    const known = new Set([...registry.panels, ...registry.widgets].map((v) => v.id));
    const restored = deserialize(serialize(layoutStore.getSnapshot().frame), known)!;
    const pane = findPaneAnywhere(restored, instanceId)!.pane;
    expect(pane.activeSection).toBe('requests');
    expect(activeSectionOf(pane)).toBe('requests');
  });

  it('falls back to the default when a stored section no longer exists', () => {
    // A layout written before a section was renamed away. Resolving rather than
    // reading is what keeps the tab strip coherent instead of selecting nothing.
    const pane = { instanceId: 's.host#9', viewId: 's.host', activeSection: 'retired' };
    expect(activeSectionOf(pane)).toBe('friends');
  });
});
