import { useState } from 'react';

import Card from '@mui/material/Card';
import CardHeader from '@mui/material/CardHeader';
import CardContent from '@mui/material/CardContent';
import CardActions from '@mui/material/CardActions';
import Button from '@mui/material/Button';

import { challengeOffer, challengeRespond, dismissOffer, useGames, type Ruleset } from '../game-ws';
import { type ChallengeTarget } from '../challenge-draft';
import { type GameCatalogEntry } from '../games-api';
import { GamesMui } from '../mui-theme';

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
    <GamesMui>
      <Card sx={{ mb: 1 }}>
        <CardHeader
          avatar={<span style={{ fontSize: '1.4rem' }}>⚔️</span>}
          title={
            <span>
              <strong>{offer.from_name}</strong> {kindLabel}: <strong>{offer.game_name}</strong>
            </span>
          }
          subheader={summarize(offer.ruleset)}
          sx={{ pb: 0.5 }}
        />
        {countering && (
          <CardContent sx={{ py: 0.5 }}>
            <RulesetEditor
              value={terms ?? offer.ruleset}
              onChange={setTerms}
              games={games}
              gameFixed
            />
          </CardContent>
        )}
        <CardActions>
          {!countering ? (
            <>
              <Button
                variant="contained"
                color="success"
                onClick={() => challengeRespond(offer.offer_id, 'accept')}
              >
                ✅ Accept
              </Button>
              <Button variant="outlined" onClick={() => setCountering(true)}>
                ✏️ Counter
              </Button>
              <Button color="inherit" onClick={() => challengeRespond(offer.offer_id, 'decline')}>
                ✖ Decline
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="contained"
                onClick={() => challengeRespond(offer.offer_id, 'counter', terms ?? offer.ruleset)}
              >
                Send counter
              </Button>
              <Button color="inherit" onClick={() => setCountering(false)}>
                Back
              </Button>
            </>
          )}
          <Button
            color="inherit"
            size="small"
            sx={{ ml: 'auto' }}
            onClick={() => dismissOffer()}
            title="Ignore (expires on its own)"
          >
            Dismiss
          </Button>
        </CardActions>
      </Card>
    </GamesMui>
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
    <GamesMui>
      <Card sx={{ mb: 1 }}>
        <CardHeader
          avatar={<span style={{ fontSize: '1.4rem' }}>⚔️</span>}
          title={
            <span>
              Challenge <strong>{target.name}</strong>
            </span>
          }
          sx={{ pb: 0.5 }}
        />
        <CardContent sx={{ py: 0.5 }}>
          <RulesetEditor value={terms} onChange={setTerms} games={games} />
        </CardContent>
        <CardActions>
          <Button
            variant="contained"
            onClick={() => {
              challengeOffer(target.accountId, terms);
              onDone();
            }}
          >
            Send challenge
          </Button>
          <Button color="inherit" onClick={onDone}>
            Cancel
          </Button>
        </CardActions>
      </Card>
    </GamesMui>
  );
}
