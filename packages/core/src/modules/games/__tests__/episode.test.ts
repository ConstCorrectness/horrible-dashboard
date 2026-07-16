import { describe, expect, it } from 'vitest';

import { applyReturns, episodeFromReplay, type EpisodeStep } from '../episode';
import type { Replay, ReplayEvent } from '../games-api';

function replay(events: ReplayEvent[], over: Partial<Replay> = {}): Replay {
  return {
    id: 'r1',
    game_id: 'tictactoe',
    table_id: 't1',
    series_id: null,
    created_at: 1_700_000_000,
    seats: ['me', 'them'],
    winner: 0,
    returns: { '0': 1, '1': -1 },
    public: false,
    events,
    ...over,
  };
}

function step(partial: Partial<EpisodeStep> & { idx: number; seat: number }): EpisodeStep {
  return {
    obs: null,
    legalActions: [],
    action: null,
    trace: [],
    state: null,
    reward: null,
    ...partial,
  };
}

describe('episodeFromReplay', () => {
  it('turns a replay event log into one step per decision', () => {
    const ep = episodeFromReplay(
      replay([
        { kind: 'action', seat: 0, action_id: '4' },
        { kind: 'public_state', state: { board: [null, null, null, null, 'X'] } },
        { kind: 'action', seat: 1, action_id: '0' },
        { kind: 'game_over', winner: 0, returns: { '0': 1, '1': -1 } },
      ]),
    );

    expect(ep.steps.map((s) => [s.seat, s.action])).toEqual([
      [0, '4'],
      [1, '0'],
    ]);
    expect(ep.live).toBe(false);
    expect(ep.replayId).toBe('r1');
    expect(ep.winner).toBe(0);
  });

  it("attaches a seat's reasoning to the step it was deciding", () => {
    const ep = episodeFromReplay(
      replay([
        {
          kind: 'trace',
          seat: 0,
          steps: [{ kind: 'assistant', tool_calls: [{ name: 'board_scanner', arguments: '{}' }] }],
        },
        { kind: 'trace', seat: 0, steps: [{ kind: 'chose', action_id: '4' }] },
        { kind: 'action', seat: 0, action_id: '4' },
      ]),
    );

    expect(ep.steps).toHaveLength(1);
    expect(ep.steps[0].trace.map((t) => t.kind)).toEqual(['assistant', 'chose']);
    expect(ep.steps[0].action).toBe('4');
  });

  it('attributes a public_state to the step that produced it', () => {
    const ep = episodeFromReplay(
      replay([
        { kind: 'action', seat: 0, action_id: '4' },
        { kind: 'public_state', state: { turn: 1 } },
        { kind: 'action', seat: 1, action_id: '0' },
        { kind: 'public_state', state: { turn: 0 } },
      ]),
    );

    expect(ep.steps[0].state).toEqual({ turn: 1 });
    expect(ep.steps[1].state).toEqual({ turn: 0 });
  });

  it('carries a timed-out move through as a timeout', () => {
    const ep = episodeFromReplay(
      replay([{ kind: 'action', seat: 1, action_id: '2', timeout: true }]),
    );
    expect(ep.steps[0].timeout).toBe(true);
  });

  it('prefers the game_over event returns over the summary field', () => {
    const ep = episodeFromReplay(
      replay([
        { kind: 'action', seat: 0, action_id: '4' },
        { kind: 'game_over', winner: 1, returns: { '0': -5, '1': 5 } },
      ]),
    );
    expect(ep.returns).toEqual({ '0': -5, '1': 5 });
    expect(ep.winner).toBe(1);
  });

  it('keeps an interleaved two-seat episode in play order', () => {
    const ep = episodeFromReplay(
      replay([
        { kind: 'action', seat: 0, action_id: 'a' },
        { kind: 'action', seat: 1, action_id: 'b' },
        { kind: 'action', seat: 0, action_id: 'c' },
      ]),
    );
    expect(ep.steps.map((s) => s.idx)).toEqual([0, 1, 2]);
    expect(ep.steps.map((s) => s.action)).toEqual(['a', 'b', 'c']);
  });
});

describe('applyReturns', () => {
  it("puts each seat's terminal reward on its own last step only", () => {
    const steps = [
      step({ idx: 0, seat: 0, action: 'a' }),
      step({ idx: 1, seat: 1, action: 'b' }),
      step({ idx: 2, seat: 0, action: 'c' }),
    ];

    const out = applyReturns(steps, { '0': 1, '1': -1 });

    expect(out.map((s) => s.reward)).toEqual([null, -1, 1]);
  });

  it('leaves steps alone when a seat has no return', () => {
    const out = applyReturns([step({ idx: 0, seat: 0, action: 'a' })], {});
    expect(out[0].reward).toBeNull();
  });
});
