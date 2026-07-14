import { useEffect, useState } from 'react';

import { apiGet, apiPut } from '../../../api';
import { fetchGamesCatalog } from '../games-api';
import { CodeEditor } from './CodeEditor';

interface LoadoutModel {
  game_id: string;
  context: string;
  tools: any[];
  model: any;
}

const DEFAULT_GAMES = [
  { id: 'tictactoe', name: 'Tic-Tac-Toe' },
  { id: 'default', name: 'default' },
];

export function LoadoutPanel() {
  const [gameId, setGameId] = useState('tictactoe');
  const [games, setGames] = useState(DEFAULT_GAMES);
  const [loadout, setLoadout] = useState<LoadoutModel | null>(null);
  const [status, setStatus] = useState('');

  useEffect(() => {
    fetchGamesCatalog().then((catalog) =>
      setGames([
        ...catalog,
        { id: 'town', name: 'AgentTown persona' },
        { id: 'default', name: 'default' },
      ]),
    );
  }, []);

  useEffect(() => {
    setStatus('loading…');
    apiGet<LoadoutModel>(`/games/loadout/${gameId}`)
      .then((l) => {
        // Initialize with default python strategy if empty or doesn't look like python code
        if (!l.context || !l.context.includes('def run')) {
          l.context = `def run(args, obs):
    import random
    # Default strategy: randomly pick one of the available actions
    actions = obs.get("legal_actions", [])
    if not actions:
        return None
    chosen = random.sample(actions, 1)[0]
    return chosen["id"]
`;
        }
        setLoadout(l);
        setStatus('');
      })
      .catch((e) => setStatus(String(e)));
  }, [gameId]);

  if (!loadout) {
    return <div style={{ padding: '0.6rem', fontSize: '0.85rem' }}>{status || 'loading…'}</div>;
  }

  const update = (patch: Partial<LoadoutModel>) => setLoadout({ ...loadout, ...patch });

  const save = async () => {
    setStatus('saving…');
    try {
      await apiPut(`/games/loadout/${gameId}`, { ...loadout, game_id: gameId });
      setStatus('saved ✓');
    } catch (e) {
      setStatus(String(e));
    }
  };

  return (
    <div
      style={{
        padding: '0.8rem',
        fontSize: '0.85rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.8rem',
        height: '100%',
        boxSizing: 'border-box',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem', flexShrink: 0 }}>
        <span style={{ fontWeight: 700 }}>Harness for</span>
        <select
          value={gameId}
          onChange={(e) => setGameId(e.target.value)}
          style={{
            padding: '0.3rem 0.5rem',
            borderRadius: '4px',
            border: '1px solid var(--border)',
            background: 'var(--bg-input, #262a32)',
            color: 'var(--text)',
            fontSize: '0.82rem',
          }}
        >
          {games.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={save}
          style={{
            padding: '0.3rem 0.8rem',
            borderRadius: '4px',
            border: 'none',
            background: 'var(--accent, #6ea8fe)',
            color: '#000',
            fontWeight: 700,
            cursor: 'pointer',
          }}
        >
          Save
        </button>
        <span style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>{status}</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', flex: 1, minHeight: 0 }}>
        <span style={{ fontWeight: 700, color: 'var(--text-dim)', fontSize: '0.78rem' }}>
          Agent Strategy (Python Code)
        </span>
        <div
          style={{
            flex: 1,
            border: '1px solid var(--border)',
            borderRadius: '4px',
            overflow: 'hidden',
            background: 'var(--bg-editor, #1e1e1e)',
          }}
        >
          <CodeEditor
            value={loadout.context}
            onChange={(val) => update({ context: val })}
            language="python"
            placeholder={`def run(args, obs):
    # Write your python strategy here
    pass`}
            minHeight="100%"
          />
        </div>
      </div>
    </div>
  );
}
