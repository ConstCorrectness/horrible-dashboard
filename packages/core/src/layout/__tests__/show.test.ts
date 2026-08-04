import { describe, expect, it } from 'vitest';

import { resolveShowTarget, showKey, type ShowCandidates } from '../show';

const CANDIDATES: ShowCandidates = {
  views: [
    { id: 'social.friends', title: 'Friends' },
    { id: 'terminal.instance', title: 'Terminal' },
    { id: 'observability.io', title: 'Data flow' },
    {
      id: 'editor.buffer',
      title: 'Editor',
      regions: [
        { id: 'code.outline', label: 'Outline' },
        { id: 'git.provenance', label: 'Provenance' },
      ],
    },
    {
      id: 'people.home',
      title: 'People',
      sections: [
        { id: 'friends', label: 'Friends' },
        { id: 'discover', label: 'Discover' },
      ],
    },
  ],
  workspaces: [
    { id: 'dataops', name: 'Data Ops' },
    { id: 'scripting', name: 'Scripting' },
  ],
  aliases: {
    'network.peers': { kind: 'view', viewId: 'people.home', section: 'discover' },
    Peers: { kind: 'view', viewId: 'people.home', section: 'discover' },
  },
};

describe('showKey', () => {
  it('ignores case, spaces and punctuation', () => {
    expect(showKey('Data Flow')).toBe(showKey('data-flow'));
    expect(showKey('Peer Chat')).toBe(showKey('peerchat'));
  });
});

describe('resolveShowTarget', () => {
  it('matches a view by id and by title', () => {
    expect(resolveShowTarget('terminal.instance', CANDIDATES)).toEqual({
      kind: 'view',
      viewId: 'terminal.instance',
    });
    expect(resolveShowTarget('Data flow', CANDIDATES)).toEqual({
      kind: 'view',
      viewId: 'observability.io',
    });
  });

  it('matches loosely, the way a user actually phrases it', () => {
    expect(resolveShowTarget('data-flow', CANDIDATES)).toEqual({
      kind: 'view',
      viewId: 'observability.io',
    });
  });

  it('reveals a region by its label', () => {
    expect(resolveShowTarget('Outline', CANDIDATES)).toEqual({
      kind: 'region',
      regionViewId: 'code.outline',
    });
  });

  it('switches workspace by name', () => {
    expect(resolveShowTarget('Data Ops', CANDIDATES)).toEqual({
      kind: 'workspace',
      workspaceId: 'dataops',
    });
  });

  it('prefers an exact view id over a fuzzy title match', () => {
    // 'Friends' is both a view title and a section label; the whole-view id wins
    // when asked for by id, and the section is still reachable by its own name.
    expect(resolveShowTarget('social.friends', CANDIDATES)).toEqual({
      kind: 'view',
      viewId: 'social.friends',
    });
  });

  it('resolves a retired name through the alias map — the reachability invariant', () => {
    // A merged-away pane must keep working forever: layouts reseed from presets, but
    // the agent's vocabulary must not regress.
    const byId = resolveShowTarget('network.peers', CANDIDATES);
    const byTitle = resolveShowTarget('Peers', CANDIDATES);
    expect(byId).toEqual({ kind: 'view', viewId: 'people.home', section: 'discover' });
    expect(byTitle).toEqual(byId);
  });

  it('refuses an ambiguous partial rather than guessing', () => {
    const ambiguous: ShowCandidates = {
      views: [
        { id: 'a.one', title: 'Report Alpha' },
        { id: 'a.two', title: 'Report Beta' },
      ],
      workspaces: [],
    };
    // Two titles contain "report" — silently picking one is how an agent opens the
    // wrong thing and then reports success.
    expect(resolveShowTarget('Report', ambiguous)).toBeNull();
  });

  it('returns null for nothing and for junk', () => {
    expect(resolveShowTarget('', CANDIDATES)).toBeNull();
    expect(resolveShowTarget('   ', CANDIDATES)).toBeNull();
    expect(resolveShowTarget('nonexistent surface', CANDIDATES)).toBeNull();
  });
});
