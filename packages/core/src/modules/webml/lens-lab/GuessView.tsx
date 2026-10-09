/**
 * "When does it know?" — a game on the window's last lens run.
 *
 * Five tokens from the reply, one at a time. For each, guess the layer at which
 * the model settled on it (the first layer from which it stays the best guess to
 * the end), then see the real column of the stack. Exact is 100 points, falling
 * to 0 a quarter of the stack away. After a few rounds you start to feel where
 * different kinds of tokens are decided: punctuation and the next word of a set
 * phrase early, the fact the question was about late.
 *
 * The best score is a per-viewer convenience, so it lives in localStorage.
 */
import { useEffect, useMemo, useState } from 'react';

import { useLensRun } from '../lens-run';
import { guessScore, guessVerdict, layerP, pickPuzzles, showToken, type Puzzle } from './model';
import { LensEmpty, Stat } from './ReplyView';

const ROUNDS = 5;
const BEST_KEY = 'webml.lensGame.best';

function readBest(): number {
  try {
    return Number(localStorage.getItem(BEST_KEY)) || 0;
  } catch {
    return 0;
  }
}

function writeBest(score: number): void {
  try {
    localStorage.setItem(BEST_KEY, String(score));
  } catch {
    // A private window: the best score is simply not kept.
  }
}

export function GuessView({ onOpenReply }: { onOpenReply: () => void }) {
  const run = useLensRun();
  const [seed, setSeed] = useState(0);
  const puzzles = useMemo<Puzzle[]>(
    () => (run && !run.live ? pickPuzzles(run.tokens, ROUNDS) : []),
    // A new game on a new run or on "Play again", not on every render.
    [run?.id, run?.live, seed],
  );
  const [round, setRound] = useState(0);
  const [guesses, setGuesses] = useState<number[]>([]);
  const [best, setBest] = useState(readBest);
  useEffect(() => {
    setRound(0);
    setGuesses([]);
  }, [puzzles]);

  if (!run || run.tokens.length === 0) return <LensEmpty />;
  if (run.live) {
    return (
      <div className="ll-empty">The model is still writing. The game starts when it stops.</div>
    );
  }
  if (puzzles.length === 0) {
    return (
      <div className="ll-empty">
        Nothing to guess in this reply: every token was either the best guess from the very first
        layer or a sampled runner-up the model never preferred. Ask the model something longer.
      </div>
    );
  }

  const total = guesses.reduce(
    (sum, g, i) => sum + guessScore(g, puzzles[i].settles, run.layers),
    0,
  );
  const finished = guesses.length === puzzles.length;
  const puzzle = puzzles[Math.min(round, puzzles.length - 1)];
  const answered = guesses.length > round;
  const t = run.tokens[puzzle.index];

  const guess = (layer: number) => {
    if (answered) return;
    const next = [...guesses, layer];
    setGuesses(next);
    if (next.length === puzzles.length) {
      const final = next.reduce((s, g, i) => s + guessScore(g, puzzles[i].settles, run.layers), 0);
      if (final > best) {
        setBest(final);
        writeBest(final);
      }
    }
  };

  // The reply up to the token, so the guess is made with what the model had.
  const before = run.tokens
    .slice(Math.max(0, puzzle.index - 24), puzzle.index)
    .map((x) => x.token)
    .join('');

  return (
    <div className="ll-view ll-game">
      <div className="ll-stats">
        <Stat label="round" value={`${Math.min(round + 1, puzzles.length)} / ${puzzles.length}`} />
        <Stat label="score" value={String(total)} />
        <Stat label="best" value={String(best)} />
        <Stat label="max" value={String(puzzles.length * 100)} />
      </div>

      <div className="ll-question">
        <div className="ll-dim">When did the model know its next token would be…</div>
        <div className="ll-context">
          <span className="ll-dim">{puzzle.index > 24 ? '…' : ''}</span>
          {before}
          <mark className="ll-target">{showToken(puzzle.token, 24)}</mark>
        </div>
      </div>

      <div className="ll-board">
        <div className="ll-layers" role="group" aria-label="Pick the layer where it settled">
          {Array.from({ length: run.layers }, (_, l) => {
            const p = layerP(t.layers[l], t.token);
            const mine = guesses[round] === l;
            const truth = answered && l === puzzle.settles;
            return (
              <button
                key={l}
                type="button"
                disabled={answered}
                className={`ll-layer${mine ? ' is-guess' : ''}${truth ? ' is-truth' : ''}${answered && l >= puzzle.settles ? ' is-settled' : ''}`}
                style={answered ? { ['--p' as string]: p.toFixed(3) } : undefined}
                onClick={() => guess(l)}
                title={
                  answered
                    ? `layer ${l}: ${t.layers[l]?.top[0] ? showToken(t.layers[l].top[0].token, 14) : '—'}`
                    : `layer ${l}`
                }
              >
                <span className="ll-layer-n">{l}</span>
                {answered && (
                  <span className="ll-layer-best">
                    {t.layers[l]?.top[0] ? showToken(t.layers[l].top[0].token, 12) : '—'}
                  </span>
                )}
                {mine && <span className="ll-layer-tag">your guess</span>}
                {truth && <span className="ll-layer-tag">settled</span>}
              </button>
            );
          })}
        </div>

        <div className="ll-result" aria-live="polite">
          {!answered ? (
            <p className="ll-dim">
              Layer 0 is the first block after the embedding; layer {run.layers - 1} is the last,
              whose readout is the model’s own output. Click a layer.
            </p>
          ) : (
            <>
              <div className="ll-verdict">{guessVerdict(guesses[round], puzzle.settles)}</div>
              <div className="ll-points">
                +{guessScore(guesses[round], puzzle.settles, run.layers)}
              </div>
              <p className="ll-dim">
                It settled at layer {puzzle.settles}. Each row now shows what that layer would have
                said; shaded rows are where <b>{showToken(puzzle.token, 16)}</b> was in the layer’s
                top five.
              </p>
              {finished ? (
                <div className="ll-final">
                  <div>
                    Final score <b>{total}</b> of {puzzles.length * 100}
                    {total >= best && total > 0 ? ' — a new best' : ''}
                  </div>
                  <div className="ll-actions">
                    <button type="button" onClick={() => setSeed((s) => s + 1)}>
                      Play again
                    </button>
                    <button type="button" onClick={onOpenReply}>
                      See the whole reply
                    </button>
                  </div>
                </div>
              ) : (
                <div className="ll-actions">
                  <button type="button" onClick={() => setRound((r) => r + 1)}>
                    Next token →
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
