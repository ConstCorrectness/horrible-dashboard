/**
 * The objective layer: a round clock, the score under its own label, the state
 * of a bomb or a flag, and a bar for whatever is being held.
 *
 * Top-centre, which is the one region of this pane's overlay nothing else uses —
 * the kill feed is top-right and the scoreboard is a held panel a little lower.
 * The same place the native client puts it, so the two read alike.
 *
 * **Everything here is drawn from what the server sent and nothing is
 * re-derived.** The score's label comes off the wire (`scoreLabel`) rather than
 * from a match on the mode id, so a mode that counts something new needs no
 * change in this file at all. The fuse and the phase clock are the server's own
 * countdowns rather than local timers started on an event, which is what keeps
 * them right after a stall. And the progress bar is `you.mode.progress`, not a
 * local one — a local timer keeps running through the interruption that resets
 * it server-side, through a stall, and through dying, each of which is a bar
 * that finishes while nothing at all happened.
 */

import type { ModeInfo, ModeSelf, ModeShared } from '../net';
import type { ObjectiveNote } from '../session';

/** CLA sand and RVSF blue, matching `TEAM_COLORS` in the panel. */
const OURS = '#e0b96a';
const THEIRS = '#7fb2e5';

export interface ModeHudProps {
  mode: ModeInfo | null;
  state: ModeShared | null;
  /** Our own half, out of `you` — the only per-recipient part of a snapshot. */
  mine: ModeSelf | null | undefined;
  scores: number[];
  /** Our team, which everything below is phrased from the point of view of. */
  team: number;
  objective: ObjectiveNote | null;
}

/**
 * A number of seconds, as a clock reads it.
 *
 * One decimal under ten and none above: a fuse with four seconds left is a
 * number you are watching tick, and a round with ninety is not.
 */
function clock(seconds: number): string {
  const s = Math.max(0, seconds);
  return s < 10 ? s.toFixed(1) : s.toFixed(0);
}

export function ModeHud({ mode, state, mine, scores, team, objective }: ModeHudProps) {
  // No mode is a different thing from an empty one, and this is where that
  // matters: a pane that rendered a default would draw a round clock reading
  // zero over a game that has no rounds.
  if (!mode || !state) return null;

  const bomb = state.bomb;
  const planted = bomb?.state === 'planted';
  const label = (mode.scoreLabel || 'score').toUpperCase();
  const [ours, theirs] = team === 0 ? [scores[0], scores[1]] : [scores[1], scores[0]];

  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: 6,
        transform: 'translateX(-50%)',
        pointerEvents: 'none',
        fontFamily: 'monospace',
        textAlign: 'center',
        lineHeight: 1.5,
        textShadow: '0 1px 2px rgba(0,0,0,0.8)',
      }}
    >
      <div
        style={{ fontSize: '1.05rem', letterSpacing: '0.14em', color: 'rgba(255,255,255,0.95)' }}
      >
        <span style={{ color: OURS }}>{ours ?? 0}</span>
        <span style={{ opacity: 0.7, margin: '0 0.6rem', fontSize: '0.72rem' }}>{label}</span>
        <span style={{ color: THEIRS }}>{theirs ?? 0}</span>
      </div>

      {/* A planted bomb's fuse replaces the round clock, because once it is down
          the round clock is not what anybody is counting. */}
      {planted ? (
        <div
          style={{
            fontSize: '0.8rem',
            letterSpacing: '0.1em',
            // The one colour change here, on the one number worth panicking
            // about.
            color: (bomb?.fuseIn ?? 0) < 5 ? '#f8635a' : '#f0ad52',
          }}
        >
          BOMB {clock(bomb?.fuseIn ?? 0)}
          {bomb?.site ? ` · ${bomb.site}` : ''}
        </div>
      ) : (
        // Absent for a mode with no phases at all, which is how deathmatch and
        // capture the flag arrive — nothing to draw and no branch to forget.
        state.phase && (
          <div
            style={{
              fontSize: '0.8rem',
              letterSpacing: '0.1em',
              color: state.phase === 'freeze' ? '#8ac4fb' : 'rgba(255,255,255,0.75)',
            }}
          >
            {state.round ? `R${state.round} · ` : ''}
            {state.phase.toUpperCase()} {clock(state.phaseIn ?? 0)}
          </div>
        )
      )}

      {/* Worth saying only in a mode that has sides *and swaps them*; in one
          that does not, it is a constant. */}
      {state.phase && mine && (
        <div
          style={{
            fontSize: '0.68rem',
            letterSpacing: '0.14em',
            color: mine.attacking ? '#f0ad52' : '#8ac4fb',
          }}
        >
          {mine.attacking ? 'ATTACK' : 'DEFEND'}
        </div>
      )}

      {mine?.carrying && (
        <div style={{ fontSize: '0.72rem', letterSpacing: '0.1em', color: '#fbd76b' }}>
          {bomb?.state ? 'YOU HAVE THE BOMB' : 'YOU HAVE THE FLAG'}
        </div>
      )}

      {(state.flags ?? []).map((flag) => {
        const isOurs = flag.team === team;
        const what =
          flag.state === 'carried'
            ? 'TAKEN'
            : flag.state === 'dropped'
              ? `DROPPED ${clock(flag.returnIn ?? 0)}`
              : 'HOME';
        return (
          <div
            key={flag.team}
            style={{
              fontSize: '0.68rem',
              letterSpacing: '0.1em',
              color:
                flag.state === 'home'
                  ? '#8fce93'
                  : flag.state === 'carried'
                    ? '#f28b7d'
                    : '#f0ad52',
            }}
          >
            {isOurs ? 'OUR FLAG' : 'ENEMY FLAG'} {what}
          </div>
        );
      })}

      {/* Above the crosshair, because the kill notice is below it and both
          landing in the same second is the ordinary case — planting while
          somebody shoots you off the site. */}
      {objective && (
        <div
          style={{
            marginTop: '0.5rem',
            fontSize: '0.9rem',
            letterSpacing: '0.16em',
            color: objective.mine ? '#9fe0a4' : '#f0ad52',
          }}
        >
          {objective.text}
        </div>
      )}
    </div>
  );
}

/**
 * The bar for a held plant or defuse.
 *
 * Separate from the block above because it belongs somewhere else on screen:
 * *under* the crosshair, not over it. What you are looking at while you plant is
 * the doorway somebody is about to come through, and a bar across the aim would
 * be the interface taking the one thing that matters away at the one moment it
 * matters.
 */
export function ModeProgress({ mine }: { mine: ModeSelf | null | undefined }) {
  // `progress` with no `progressKind` is a half-populated blob rather than a
  // half-finished action; drawing an unlabelled bar for it would invent a state
  // the server never described.
  if (!mine?.progress || !mine.progressKind) return null;
  const colour = mine.progressKind === 'defuse' ? '#6fc0f5' : '#f0ad52';
  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: '62%',
        transform: 'translateX(-50%)',
        pointerEvents: 'none',
        textAlign: 'center',
        fontFamily: 'monospace',
        width: 220,
      }}
    >
      <div
        style={{
          fontSize: '0.7rem',
          letterSpacing: '0.18em',
          color: colour,
          marginBottom: '0.3rem',
          textShadow: '0 1px 2px rgba(0,0,0,0.8)',
        }}
      >
        {mine.progressKind.toUpperCase()}
      </div>
      <div style={{ height: 6, background: 'rgba(13,17,23,0.75)', borderRadius: 3 }}>
        <div
          style={{
            height: '100%',
            width: `${Math.min(1, Math.max(0, mine.progress)) * 100}%`,
            background: colour,
            borderRadius: 3,
          }}
        />
      </div>
    </div>
  );
}
