import { useEffect, useMemo, useState } from 'react';

import {
  fetchEnvInfo,
  runTraining,
  type EnvInfo,
  type CodedHarness,
  type TrainRunResult,
} from '../games-api';

/**
 * **The self-play runner** — Training's fast inner loop.
 *
 * A rated match tells you one bit every couple of minutes. Two hundred headless
 * episodes tell you a win rate in about a second, and that difference is the whole
 * reason this exists: it is the loop you actually iterate in.
 *
 * It runs the same `HorribleEnv` you would train against in a notebook and the same
 * bot shapes a ranked seat runs, so the number here means what it appears to mean.
 * Three readouts earn their place:
 *
 * - **Illegal actions** are reported, never repaired. Choosing outside
 *   `info["action_mask"]` is the single most common bug in a new policy, and the
 *   tempting kindness of substituting a legal move would hide it behind a slightly
 *   worse win rate.
 * - **The reward curve** is what tells a *learning* agent apart from a fixed one —
 *   a flat line means your `observe()` isn't doing anything.
 * - **Seats alternate every episode**, so the win rate is a win rate rather than a
 *   measure of how good you are at going first.
 *
 * Games with no environment (every `reasoner` game) get an explanation instead of a
 * broken runner. See docs/modules/games.mdx.
 */

const OPPONENTS: { value: string; label: string }[] = [
  { value: 'random', label: '🎲 Random' },
  { value: 'bot:bronze', label: '🥉 Rusty' },
  { value: 'bot:silver', label: '🥈 Circuit' },
  { value: 'bot:gold', label: '🥇 Aurum' },
  { value: 'bot:platinum', label: '💠 Nemesis' },
];

/**
 * The code Training runs: the coded harness's policy, whole.
 *
 * This used to mirror a name-resolution rule (`<gameId>.bot`, else `bot`, out of
 * the LLM harness's tool list) that had to agree with the backend's or Training
 * would measure something the ladder never runs. The coded harness has one body
 * and no names, so there is nothing left to keep in sync. A null harness means it
 * hasn't loaded yet — never "there is nothing to run": the backend guarantees code
 * comes back, a random-legal-move baseline when you haven't written one.
 */
export function botToolOf(
  harness: CodedHarness | null,
  gameId: string,
): { name: string; code: string } | null {
  if (!harness?.bot_code) return null;
  return { name: `${gameId} bot`, code: harness.bot_code };
}

function Sparkline({ points }: { points: number[] }) {
  if (points.length < 2) return null;
  // Reward is in [-1, 1]; map to a fixed baseline so runs are comparable and a
  // curve that never leaves the floor visibly never leaves the floor.
  const w = 100;
  const h = 28;
  const step = w / (points.length - 1);
  const y = (v: number) => h - ((Math.max(-1, Math.min(1, v)) + 1) / 2) * h;
  const d = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(2)},${y(p).toFixed(2)}`)
    .join(' ');
  return (
    <svg
      className="games-train-spark"
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Mean reward over the run"
    >
      <line x1="0" y1={h / 2} x2={w} y2={h / 2} className="games-train-spark-zero" />
      <path d={d} className="games-train-spark-line" />
    </svg>
  );
}

export function TrainingRunner({
  gameId,
  loadout,
}: {
  gameId: string;
  loadout: CodedHarness | null;
}) {
  const [env, setEnv] = useState<EnvInfo | null>(null);
  const [opponent, setOpponent] = useState('bot:bronze');
  const [episodes, setEpisodes] = useState(200);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<TrainRunResult | null>(null);
  const [showSample, setShowSample] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setEnv(null);
    setResult(null);
    fetchEnvInfo(gameId)
      .then((e) => {
        if (cancelled) return;
        setEnv(e);
        if (e.training) setEpisodes(e.training.default_episodes);
      })
      .catch(() => !cancelled && setEnv(null));
    return () => {
      cancelled = true;
    };
  }, [gameId]);

  const bot = useMemo(() => botToolOf(loadout, gameId), [loadout, gameId]);

  const run = async () => {
    if (!bot) return;
    setRunning(true);
    setShowSample(false);
    try {
      setResult(await runTraining({ game_id: gameId, code: bot.code, opponent, episodes }));
    } catch (e) {
      setResult({
        ok: false,
        error: String(e),
        shape: '',
        episodes: 0,
        wins: 0,
        draws: 0,
        losses: 0,
        illegal: 0,
        truncated: 0,
        mean_reward: 0,
        curve: [],
        elapsed_ms: 0,
        stopped_early: false,
        sample: null,
      });
    } finally {
      setRunning(false);
    }
  };

  if (env && !env.has_env) {
    return (
      <div className="games-train-noenv">
        <strong>No RL environment for this game.</strong>
        <p>{env.reason}</p>
      </div>
    );
  }

  // Only ever a loading state: the backend injects a default bot tool into every
  // loadout it serves, so "no bot" is not a reachable steady state.
  if (!bot) return <div className="games-train-loading">Loading harness…</div>;

  const pct = (n: number) =>
    result && result.episodes ? Math.round((n / result.episodes) * 100) : 0;

  return (
    <div className="games-train-runner">
      <div className="games-train-controls">
        <label>
          <span>opponent</span>
          <select value={opponent} onChange={(e) => setOpponent(e.target.value)}>
            {OPPONENTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>episodes</span>
          <input
            type="number"
            min={1}
            max={env?.training?.max_episodes ?? 5000}
            value={episodes}
            onChange={(e) => setEpisodes(Math.max(1, Number(e.target.value) || 1))}
          />
        </label>
        <button
          type="button"
          className="games-train-run"
          onClick={() => void run()}
          disabled={running}
        >
          {running ? 'Running…' : '▶ Run'}
        </button>
        <span className="games-train-toolname">
          running <code>{bot.name}</code>
        </span>
      </div>

      {env?.training?.hint && <p className="games-train-hint">💡 {env.training.hint}</p>}

      {result && !result.ok && <div className="games-train-error">{result.error}</div>}

      {result?.ok && (
        <>
          <div className="games-train-results">
            <div className="games-train-wdl">
              <span className="win">{pct(result.wins)}% W</span>
              <span className="draw">{pct(result.draws)}% D</span>
              <span className="loss">{pct(result.losses)}% L</span>
            </div>
            <div className="games-train-bar" aria-hidden>
              <span className="win" style={{ width: `${pct(result.wins)}%` }} />
              <span className="draw" style={{ width: `${pct(result.draws)}%` }} />
              <span className="loss" style={{ width: `${pct(result.losses)}%` }} />
            </div>
            <div className="games-train-meta">
              <span>
                {result.episodes} episodes · {result.elapsed_ms}ms · shape{' '}
                <code>{result.shape === 'run' ? 'legacy run()' : result.shape}</code>
              </span>
              <span>
                mean reward {result.mean_reward >= 0 ? '+' : ''}
                {result.mean_reward.toFixed(2)}
              </span>
            </div>
          </div>

          {result.curve.length > 1 && (
            <div className="games-train-curve">
              <Sparkline points={result.curve} />
              <span className="games-train-curve-label">
                reward over the run — flat means nothing is learning
              </span>
            </div>
          )}

          {result.illegal > 0 && (
            <div className="games-train-illegal">
              ✗ {result.illegal} illegal action{result.illegal === 1 ? '' : 's'} — your bot chose
              outside <code>info["action_mask"]</code>. Those episodes ended at −1.
            </div>
          )}
          {result.stopped_early && (
            <div className="games-train-illegal">
              ⏱ Stopped at the time budget after {result.episodes} episodes.
            </div>
          )}

          {result.sample && (
            <div className="games-train-sample">
              <button type="button" onClick={() => setShowSample((s) => !s)}>
                {showSample ? '▾' : '▸'}{' '}
                {result.sample.reward < 0 ? 'watch a losing episode' : 'watch an episode'} (#
                {result.sample.episode}, seat {result.sample.seat})
              </button>
              {showSample && (
                <ol className="games-train-moves">
                  {result.sample.moves.map((m, i) => (
                    <li key={i} className={m.illegal ? 'illegal' : undefined}>
                      {m.illegal ? `illegal — returned ${m.returned}` : `played ${m.action}`}
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
