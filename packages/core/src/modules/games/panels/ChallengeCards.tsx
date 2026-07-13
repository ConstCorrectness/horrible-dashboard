import { useState } from 'react';

import {
  challengeOffer,
  challengeRespond,
  dismissOffer,
  gamesQueueLeave,
  useGames,
  type Ruleset,
} from '../game-ws';
import { findRankedMatch } from '../matchmaking';
import { type ChallengeTarget } from '../challenge-draft';
import { type GameCatalogEntry } from '../games-api';

const DEFAULT_TERMS: Omit<Ruleset, 'game_id'> = {
  best_of: 1,
  difficulty: 'standard',
  move_timeout_s: null,
  edit_phase_s: 0,
  model_class: 'any',
  rated: true,
};

/** The negotiable terms of a battle — shared by the draft form and the counter. */
function RulesetEditor({
  value,
  onChange,
  games,
  gameFixed,
}: {
  value: Ruleset;
  onChange: (r: Ruleset) => void;
  games: GameCatalogEntry[];
  gameFixed?: boolean;
}) {
  return (
    <div className="games-ruleset-editor">
      {!gameFixed && (
        <label>
          game{' '}
          <select
            value={value.game_id}
            onChange={(e) => onChange({ ...value, game_id: e.target.value })}
          >
            {games.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        format{' '}
        <select
          value={value.best_of}
          onChange={(e) => onChange({ ...value, best_of: Number(e.target.value) })}
        >
          <option value={1}>Bo1</option>
          <option value={3}>Bo3</option>
          <option value={5}>Bo5</option>
        </select>
      </label>
      <label>
        difficulty{' '}
        <select
          value={value.difficulty}
          onChange={(e) => onChange({ ...value, difficulty: e.target.value })}
        >
          <option value="standard">standard</option>
          <option value="hard">hard</option>
          <option value="expert">expert</option>
        </select>
      </label>
      <label>
        models{' '}
        <select
          value={value.model_class}
          onChange={(e) =>
            onChange({ ...value, model_class: e.target.value as Ruleset['model_class'] })
          }
        >
          <option value="any">any model</option>
          <option value="local">local only</option>
        </select>
      </label>
      {value.best_of > 1 && (
        <label>
          edit window{' '}
          <select
            value={value.edit_phase_s}
            onChange={(e) => onChange({ ...value, edit_phase_s: Number(e.target.value) })}
          >
            <option value={0}>none</option>
            <option value={60}>1 min</option>
            <option value={180}>3 min</option>
            <option value={600}>10 min</option>
          </select>
        </label>
      )}
      <label title="rated matches move your ladder rating">
        <input
          type="checkbox"
          checked={value.rated}
          onChange={(e) => onChange({ ...value, rated: e.target.checked })}
        />{' '}
        rated
      </label>
    </div>
  );
}

function summarize(r: Ruleset): string {
  const bits = [
    `Bo${r.best_of}`,
    r.difficulty,
    r.rated ? 'rated' : 'casual',
    r.model_class === 'local' ? 'local models only' : null,
    r.best_of > 1 && r.edit_phase_s > 0 ? `${Math.round(r.edit_phase_s / 60)}m edit window` : null,
  ];
  return bits.filter(Boolean).join(' · ');
}

/** An incoming challenge/rematch/counter: accept, decline, or counter with edits. */
export function IncomingOfferCard({ games }: { games: GameCatalogEntry[] }) {
  const { offer } = useGames();
  const [countering, setCountering] = useState(false);
  const [terms, setTerms] = useState<Ruleset | null>(null);
  if (!offer) return null;
  const kindLabel =
    offer.kind === 'rematch'
      ? 'wants a rematch'
      : offer.kind === 'counter'
        ? 'counters'
        : 'challenges you';

  return (
    <div className="games-offer-card">
      <div className="games-offer-head">
        ⚔️ <strong>{offer.from_name}</strong> {kindLabel}: <strong>{offer.game_name}</strong>
        <span style={{ color: 'var(--text-dim)' }}> — {summarize(offer.ruleset)}</span>
      </div>
      {countering && (
        <RulesetEditor value={terms ?? offer.ruleset} onChange={setTerms} games={games} gameFixed />
      )}
      <div className="games-offer-actions">
        {!countering ? (
          <>
            <button type="button" onClick={() => challengeRespond(offer.offer_id, 'accept')}>
              ✅ Accept
            </button>
            <button type="button" onClick={() => setCountering(true)}>
              ✏️ Counter
            </button>
            <button type="button" onClick={() => challengeRespond(offer.offer_id, 'decline')}>
              ✖ Decline
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => challengeRespond(offer.offer_id, 'counter', terms ?? offer.ruleset)}
            >
              Send counter
            </button>
            <button type="button" onClick={() => setCountering(false)}>
              Back
            </button>
          </>
        )}
        <button type="button" onClick={() => dismissOffer()} title="Ignore (expires on its own)">
          Dismiss
        </button>
      </div>
    </div>
  );
}

/** Draft an outgoing challenge at a specific player (opened by the roster's ⚔️). */
export function ChallengeDraftCard({
  target,
  games,
  onDone,
}: {
  target: ChallengeTarget;
  games: GameCatalogEntry[];
  onDone: () => void;
}) {
  const [terms, setTerms] = useState<Ruleset>({
    game_id: games[0]?.id ?? 'tictactoe',
    ...DEFAULT_TERMS,
  });
  return (
    <div className="games-offer-card">
      <div className="games-offer-head">
        ⚔️ Challenge <strong>{target.name}</strong>
      </div>
      <RulesetEditor value={terms} onChange={setTerms} games={games} />
      <div className="games-offer-actions">
        <button
          type="button"
          onClick={() => {
            challengeOffer(target.accountId, terms);
            onDone();
          }}
        >
          Send challenge
        </button>
        <button type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** The ranked hero: pick a game + difficulty and Find Match (live queue timer). */
export function RankedCard({ games }: { games: GameCatalogEntry[] }) {
  const { queue, lastRating } = useGames();
  const [gameId, setGameId] = useState('tictactoe');
  const [difficulty, setDifficulty] = useState('standard');

  if (queue) {
    return (
      <div className="games-ranked-card">
        <span className="games-ranked-title">🏁 Ranked</span>
        <span>
          Searching {queue.gameId} ({queue.difficulty})… {queue.waitingS}s
          <span style={{ color: 'var(--text-dim)' }}> · window ±{Math.round(queue.window)}</span>
        </span>
        <button type="button" onClick={() => gamesQueueLeave()}>
          Cancel
        </button>
      </div>
    );
  }
  return (
    <div className="games-ranked-card">
      <span className="games-ranked-title">🏁 Ranked</span>
      <select value={gameId} onChange={(e) => setGameId(e.target.value)}>
        {games.map((g) => (
          <option key={g.id} value={g.id}>
            {g.name}
          </option>
        ))}
      </select>
      <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
        <option value="standard">standard</option>
        <option value="hard">hard 🔒gold</option>
        <option value="expert">expert 🔒diamond</option>
      </select>
      <button
        type="button"
        className="games-play-btn"
        onClick={() => void findRankedMatch(gameId, difficulty)}
      >
        Find match
      </button>
      {lastRating?.tier && (
        <span className="games-tier-chip" title={`after your last ${lastRating.game_id} game`}>
          {lastRating.tier}
          {lastRating.rating ? ` · ${Math.round(lastRating.rating)}` : ''}
        </span>
      )}
    </div>
  );
}
