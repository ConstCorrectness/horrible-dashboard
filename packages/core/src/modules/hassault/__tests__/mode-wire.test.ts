/**
 * The mode wire, against a payload the **real server actually produced**.
 *
 * `apps/native-fps/tests/mode-wire.json` is a welcome and a snapshot captured
 * out of a live defuse room mid-round, bomb planted — not written by hand. Both
 * clients read the same file, which is the point: it is the one artefact that
 * can catch the two of them drifting apart from each other *and* from the
 * server, and a fixture written to match the code it tests can catch neither.
 *
 * Everything on these interfaces is optional, as it has to be so an older server
 * does not break a newer client. The cost of that is exactly this: a renamed
 * key, a `camelCase` slip or a struct nested one level differently all read as
 * `undefined` with no error anywhere. The symptom is a HUD drawing nothing over
 * a round that is really happening.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { Fx, ModeInfo, ModeSelf, ModeShared, Snapshot } from '../net';
import { SUPPORTED_MODE_V } from '../net';
import { objectiveNote } from '../session';

const FIXTURE = join(__dirname, '../../../../../../apps/native-fps/tests/mode-wire.json');

interface Wire {
  welcome: { data: Record<string, unknown> & { mode?: ModeInfo & ModeShared } };
  snapshot: { data: Snapshot };
}

function wire(): Wire {
  return JSON.parse(readFileSync(FIXTURE, 'utf8')) as Wire;
}

describe('the mode wire, from a captured payload', () => {
  it('carries the mode and its static configuration in the welcome', () => {
    const mode = wire().welcome.data.mode;
    expect(mode).toBeDefined();
    expect(mode?.id).toBe('defuse');
    // Rendered verbatim by both HUDs, so a mode counting something new needs no
    // client change at all.
    expect(mode?.scoreLabel).toBe('Rounds');
    expect(mode?.teams).toBe(true);
    expect(mode?.v).toBe(SUPPORTED_MODE_V);
  });

  it('serves the timings rather than leaving a client to guess them', () => {
    // A plant bar that finishes early is a bar drawn against a number the client
    // made up.
    const config = wire().welcome.data.mode?.config;
    expect(config?.plantTime).toBeGreaterThan(0);
    expect(config?.defuseTime).toBeGreaterThan(0);
    expect(config?.fuseTime).toBeGreaterThan(0);
    expect(config?.roundsToWin).toBeGreaterThan(0);
  });

  it('resolves sites onto the floor server-side', () => {
    const sites = wire().welcome.data.mode?.sites ?? [];
    expect(sites.length).toBeGreaterThan(0);
    for (const site of sites) {
      expect(site.id).not.toBe('');
      expect(site.radius).toBeGreaterThan(0);
    }
  });

  it('flattens the current state into the welcome beside the static half', () => {
    // Which is what gives the pane a real phase on the *first* frame instead of
    // a blank one until the next snapshot. Joining mid-round otherwise shows a
    // round clock reading zero, which looks like the round having just ended.
    const mode = wire().welcome.data.mode;
    expect(mode?.phase).toBeTruthy();
    expect(mode?.round).toBeGreaterThan(0);
  });

  it('carries the public mode state on a snapshot', () => {
    const mode: ModeShared | undefined = wire().snapshot.data.mode;
    expect(mode?.phase).toBe('live');
    expect(mode?.round).toBeGreaterThan(0);
    expect(mode?.bomb?.state).toBe('planted');
    expect(mode?.bomb?.site).toBe('A');
    // `fuseIn`, not `fuse_in`: the rename is exactly the kind of slip that reads
    // as `undefined` and draws a fuse of zero.
    expect(mode?.bomb?.fuseIn).toBeGreaterThan(0);
  });

  it('keeps the per-recipient half inside `you` and out of the shared blob', () => {
    // The client-side half of the rule the server documents as its most
    // dangerous mistake: anything per recipient placed in the shared state is
    // sent to everybody, and nothing raises, warns or breaks the snapshot
    // template. If these ever start arriving in `mode` instead of `you.mode`,
    // this fails rather than quietly reading them from the wrong place.
    const data = wire().snapshot.data;
    const mine: ModeSelf | undefined = data.you?.mode;
    expect(mine).toBeDefined();
    // Captured from the defender's envelope on the round the attackers planted.
    expect(mine?.attacking).toBe(false);
    expect(mine?.carrying).toBe(false);
    const shared = data.mode as Record<string, unknown> | undefined;
    expect(shared?.progress).toBeUndefined();
    expect(shared?.attacking).toBeUndefined();
  });
});

describe('the buy catalogue', () => {
  it('arrives with the welcome, priced by the server', () => {
    const mode = wire().welcome.data.mode;
    const catalog = mode?.catalog ?? [];
    expect(catalog.length).toBeGreaterThan(0);
    expect(mode?.config?.startMoney).toBeGreaterThan(0);
    for (const item of catalog) {
      expect(item.id).not.toBe('');
      expect(item.name, 'a row with no name is a blank line').not.toBe('');
      expect(item.price).toBeGreaterThan(0);
      expect(['weapon', 'armour', 'nade']).toContain(item.kind);
    }
  });

  it('has no duplicate ids, because the index is what goes on the wire', () => {
    const ids = (wire().welcome.data.mode?.catalog ?? []).map((i) => i.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('leaves the knife and pistol out, since you always have them', () => {
    const slots = (wire().welcome.data.mode?.catalog ?? [])
      .filter((i) => i.kind === 'weapon')
      .map((i) => i.slot);
    expect(slots).not.toContain(0);
    expect(slots).not.toContain(1);
  });

  it('puts the purse and what it bought in that player own envelope', () => {
    // Captured from the buyer on the tick after they bought the first entry.
    // The defender's envelope in the same fixture has neither, which is the
    // point: this is per recipient, and in the shared blob it would be every
    // player's money.
    const raw = JSON.parse(readFileSync(FIXTURE, 'utf8')) as {
      buyerYou: { mode: ModeSelf };
    };
    expect(raw.buyerYou.mode.attacking).toBe(true);
    expect(raw.buyerYou.mode.money).toBeGreaterThan(0);
    expect(raw.buyerYou.mode.bought).toEqual([0]);

    const defender = wire().snapshot.data.you?.mode;
    expect(defender?.attacking).toBe(false);
    expect(defender?.bought).toEqual([]);
  });
});

describe('objective phrasing', () => {
  it('reads a flag taken by us differently from one taken from us', () => {
    const mine = objectiveNote({ kind: 'flag_take', by: 'me' } as Fx, 'me');
    const theirs = objectiveNote({ kind: 'flag_take', by: 'them' } as Fx, 'me');
    expect(mine?.text).not.toBe(theirs?.text);
    expect(mine?.mine).toBe(true);
    expect(theirs?.mine).toBe(false);
  });

  it('names the site a bomb was planted on when the server said which', () => {
    expect(objectiveNote({ kind: 'bomb_planted', detail: 'A' } as Fx, 'me')?.text).toContain('A');
    expect(objectiveNote({ kind: 'bomb_planted' } as Fx, 'me')?.text).toBe('BOMB PLANTED');
  });

  it('says nothing when a round goes live', () => {
    // The phase clock already says LIVE, and a banner that only repeats a
    // readout is one people learn to ignore — including on the events that
    // matter.
    expect(objectiveNote({ kind: 'round_live' } as Fx, 'me')).toBeNull();
  });

  it('is null for everything that is not an objective event', () => {
    // Which is what lets `absorb` use it as the test as well as the phrasing. A
    // second list of the same fourteen kinds is exactly the pair that drifts.
    expect(objectiveNote({ kind: 'spawn', id: 'x' } as Fx, 'me')).toBeNull();
    expect(
      objectiveNote(
        {
          kind: 'kill',
          victim: 'a',
          victimName: 'A',
          killer: 'b',
          killerName: 'B',
          head: false,
          weapon: '',
        } as Fx,
        'me',
      ),
    ).toBeNull();
  });

  it('phrases every objective kind the server can send', () => {
    // A kind with no phrasing renders no banner at all — silent, and only on the
    // event nobody happened to test.
    const kinds = [
      'flag_take',
      'flag_drop',
      'flag_return',
      'capture',
      'bomb_planted',
      'bomb_defused',
      'bomb_exploded',
      'round_start',
      'round_end',
      'eliminated',
      'time_out',
      'half',
      'match_over',
    ] as const;
    for (const kind of kinds) {
      const note = objectiveNote({ kind } as Fx, 'me');
      expect(note, `${kind} has no phrasing`).not.toBeNull();
      expect(note?.text.length, `${kind} phrased as an empty string`).toBeGreaterThan(0);
    }
  });
});
