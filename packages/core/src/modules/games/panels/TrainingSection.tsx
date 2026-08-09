import { useEffect, useState } from 'react';

import { gameAccent, gameIcon } from '../game-identity';
import {
  fetchGamesCatalog,
  getLoadout,
  type CodedHarness,
  type GameCatalogEntry,
  type LlmHarness,
} from '../games-api';
import { openGamesSection } from '../hub-section';
import { openHarnessFor, useActiveGame } from '../selected-game';
import { DryRunSection } from './DryRunSection';
import { ObservationInspector } from './ObservationInspector';
import { TrainingRunner } from './TrainingRunner';

/**
 * **Training** — the no-stakes room, and a peer of Play rather than a corner of the
 * builder.
 *
 * Fighting games have had this for thirty years for a reason: the loop you actually
 * iterate in is *set up a position → run one turn → read what happened → change
 * something → run it again*, and it has nothing to do with an opponent. Dry-run
 * already did the hard part; it was buried three scrolls into the Build section,
 * where you only found it if you already knew it existed.
 *
 * Two halves, in the order you need them: **what the engine hands your agent**
 * (`ObservationInspector` — cheap, no model, resample by seed) and **what your agent
 * does with it** (`DryRunSection` — the whole loadout, the real model, the full
 * trace, and crucially *no random fallback*, so a broken harness fails visibly here
 * instead of quietly playing a random move in a live match).
 *
 * The loadout is fetched **saved**, not draft: this section is "what would my agent
 * do right now", which is the saved harness. The builder's own dry-run panel remains
 * the one that tests unsaved edits. See docs/modules/games.mdx.
 */
export function TrainingSection() {
  const activeGame = useActiveGame();
  const [games, setGames] = useState<GameCatalogEntry[]>([]);
  const [gameId, setGameId] = useState(activeGame ?? 'tictactoe');
  // This section spans BOTH harnesses, because its two halves are different
  // questions: "run my policy a few hundred times" is the coded one (an LLM
  // harness has no policy to run headless), and "watch one turn think" is the LLM
  // one (a coded bot has no thinking to watch). So both are loaded, and each block
  // renders only when the game's seats can actually run that harness.
  const [coded, setCoded] = useState<CodedHarness | null>(null);
  const [llm, setLlm] = useState<LlmHarness | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchGamesCatalog()
      .then(setGames)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (activeGame) setGameId(activeGame);
  }, [activeGame]);

  useEffect(() => {
    let cancelled = false;
    setCoded(null);
    setLlm(null);
    setError(null);
    getLoadout(gameId, 'coded')
      .then((h) => !cancelled && setCoded(h as CodedHarness))
      .catch((e: Error) => !cancelled && setError(String(e.message || e)));
    getLoadout(gameId, 'llm')
      .then((h) => !cancelled && setLlm(h as LlmHarness))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [gameId]);

  const entry = games.find((g) => g.id === gameId);
  // Whether this game's seats may run the LLM harness at all — the same rule the
  // backend applies to /games/dry-run, keyed on allowed_policies rather than the
  // category so a turn-based coded game on the escape hatch still gets one.
  const canReason = (entry?.allowed_policies ?? ['agent']).includes('agent');
  const accent = gameAccent(gameId);

  return (
    <div className="games-train-root" style={{ '--tile-accent': accent } as React.CSSProperties}>
      <div className="games-train-head">
        <span className="games-train-glyph" aria-hidden>
          🎯
        </span>
        <div className="games-train-headings">
          <h2>Training</h2>
          <p>
            The no-stakes room. See what your agent is handed, play it a few hundred times against a
            sparring partner, and watch a single turn think. Nothing here is rated and nothing is
            uploaded — and there is no random fallback, so a broken harness fails visibly instead of
            quietly playing a random move.
          </p>
        </div>
        <select
          className="games-train-game"
          value={gameId}
          onChange={(e) => setGameId(e.target.value)}
          aria-label="Game to train against"
        >
          {(games.length ? games : [{ id: gameId, name: gameId }]).map((g) => (
            <option key={g.id} value={g.id}>
              {gameIcon(g.id)} {g.name}
            </option>
          ))}
        </select>
        <button type="button" className="games-train-edit" onClick={() => openHarnessFor(gameId)}>
          🛠 Edit harness
        </button>
      </div>

      <section className="games-train-block">
        <h3>1 · What your agent is handed</h3>
        <ObservationInspector gameId={gameId} obsKind={entry?.obs_kind} />
      </section>

      <section className="games-train-block">
        <h3>2 · Run it a few hundred times</h3>
        <TrainingRunner gameId={gameId} loadout={coded} />
      </section>

      <section className="games-train-block">
        <h3>3 · Watch one turn think</h3>
        {!canReason ? (
          // Mirrors the backend's own refusal on /games/dry-run: this game has no
          // model in its loop, so there is no turn to watch think.
          <div className="games-train-noenv">
            <strong>No model in this game’s loop.</strong>
            <p>
              {entry?.name ?? gameId} is a coded-agent game — its seat runs your policy, not a
              prompt. Step 2 above is where you watch it play.
            </p>
          </div>
        ) : error ? (
          <div className="games-train-error">
            Couldn’t load the saved harness for {gameId}: {error}
          </div>
        ) : !llm ? (
          <div className="games-train-loading">Loading harness…</div>
        ) : (
          <DryRunSection
            gameId={gameId}
            loadout={{ context: llm.context, tools: llm.tools, model: llm.model }}
            engineGames={games.map((g) => ({ id: g.id, name: g.name }))}
          />
        )}
      </section>

      <button type="button" className="games-train-back" onClick={() => openGamesSection('play')}>
        ← Back to Play
      </button>
    </div>
  );
}
