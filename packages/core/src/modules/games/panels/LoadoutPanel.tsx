import { useCallback, useEffect, useState, type CSSProperties } from 'react';

import { apiDelete, apiGet, apiPost, apiPut } from '../../../api';
import { registry } from '../../../registry';
import type { EditorService } from '../../editor/service';
import { fetchGamesCatalog } from '../games-api';
import { CodeEditor } from './CodeEditor';
import { DryRunSection } from './DryRunSection';

/** The editor's buffer surface, looked up lazily (the editor module registers it
 * at load). Undefined only if the editor module never loaded — the harness panel
 * then simply hides its "edit in editor" affordances. */
const editor = (): EditorService | undefined => registry.getService<EditorService>('editor');

const labelStyle: CSSProperties = {
  display: 'block',
  fontSize: '0.7rem',
  color: 'var(--text-dim)',
};

/**
 * The **agent harness editor** — where the skill of this game actually lives. A
 * player writes their agent's strategy `context` and a set of custom tools (real
 * Python `run(args, obs)` functions) that the agent may call while deciding a move.
 * Better tools ⇒ a better agent. Tools run only on this node and only ever see this
 * seat's observation. See docs/modules/games.mdx (agent harness).
 */

interface ToolDef {
  name: string;
  description: string;
  code: string;
  parameters: Record<string, unknown>;
  required: string[];
}
interface ModelConfig {
  provider: 'anthropic' | 'openai' | 'ollama';
  model: string;
  endpoint?: string | null;
  api_key_name?: string | null;
}
interface LoadoutModel {
  game_id: string;
  context: string;
  tools: ToolDef[];
  /** null = borrow the agent module's configured model. */
  model: ModelConfig | null;
}
interface VersionInfo {
  id: string;
  label: string;
  created_at: number;
  active: boolean;
}
type VersionStats = Record<string, { win: number; loss: number; draw: number }>;

// `default` is the fallback harness used when a game has no game-specific loadout.
const DEFAULT_GAMES = [
  { id: 'tictactoe', name: 'Tic-Tac-Toe' },
  { id: 'default', name: 'default' },
];

const STARTER_CODE = `def run(args, obs):
    # obs = your seat's observation (e.g. obs["board"] for tic-tac-toe).
    # args = the arguments the model passed. Return anything JSON-serializable.
    return {"note": "describe what this tool computes"}
`;

function newTool(n: number, gameId: string): ToolDef {
  let code = STARTER_CODE;
  if (gameId === 'tictactoe') {
    code = `def run(args, obs):
    # Tic-tac-toe bot. obs["board"] is a list of 9 cells (None, "X", "O").
    # Return a cell index string "0"-"8".
    import random
    legals = [str(a["id"]) for a in obs.get("legal_actions", [])]
    return random.choice(legals) if legals else "4"
`;
  } else if (gameId === 'connect_four') {
    code = `def run(args, obs):
    # Connect Four bot. Return column index string "0"-"6".
    import random
    legals = [str(a["id"]) for a in obs.get("legal_actions", [])]
    return random.choice(legals) if legals else "3"
`;
  } else if (gameId === 'holdem') {
    code = `def run(args, obs):
    # Texas Hold'em bot. Return action string (e.g. "check", "call", "fold").
    import random
    legals = [str(a["id"]) for a in obs.get("legal_actions", [])]
    return random.choice(legals) if legals else "check"
`;
  } else if (gameId === 'arena') {
    code = `def run(args, obs):
    # Arena bot. Return direction string: "up", "down", "left", "right", "stay".
    import random
    legals = [str(a["id"]) for a in obs.get("legal_actions", [])]
    return random.choice(legals) if legals else "stay"
`;
  } else if (gameId === 'fighter') {
    code = `def run(args, obs):
    # Fighter 2D bot. Return action string: "left", "right", "jump", "block", "light", "heavy", "special", "idle".
    import random
    legals = [str(a["id"]) for a in obs.get("legal_actions", [])]
    return random.choice(legals) if legals else "idle"
`;
  } else if (gameId === 'bug_hunt') {
    code = `def run(args, obs):
    # Bug Hunt bot. Return a patch dictionary mapping filepath to new contents.
    # e.g., return {"math_utils.py": "def add(a, b):\\n    return a + b"}
    return {}
`;
  } else if (gameId === 'code_golf') {
    code = `def run(args, obs):
    # Code Golf bot. Return Python source code to solve the challenge.
    return "def solve():\\n    pass"
`;
  } else if (gameId === 'test_duel') {
    code = `def run(args, obs):
    # Test Duel bot. Return implementation or test code string depending on obs["phase"].
    return "def pow(b, e):\\n    return b ** e"
`;
  } else if (gameId === 'town') {
    code = `def run(args, obs):
    # AgentTown resident decision bot. Return action dictionary.
    # e.g., return {"action": "move", "place": "gym"}
    import random
    places = obs.get("places", [])
    if places:
        return {"action": "move", "place": random.choice(places)}
    return {"action": "rest"}
`;
  } else if (gameId === 'tabular_fe') {
    code = `def run(args, obs):
    # Tabular Feature Engineering bot. Return the python solution.py source code.
    # It must contain a transform(df) function that takes a DataFrame and returns a cleaned, numeric-only DataFrame.
    # Look at obs["data_samples"] to understand the schema.
    return """import pandas as pd
import numpy as np

def transform(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Impute missing values
    if 'distance' in df.columns:
        df['distance'] = df['distance'].fillna(600.0)
    if 'fuel_density' in df.columns:
        df['fuel_density'] = df['fuel_density'].fillna(0.8)
        
    # Map categoricals to numeric integers
    exp_map = {'rookie': 0, 'veteran': 1, 'elite': 2}
    if 'crew_experience' in df.columns:
        df['crew_experience'] = df['crew_experience'].map(exp_map).fillna(0)
        
    ship_map = {'light_freighter': 0, 'heavy_cruiser': 1, 'mining_barge': 2}
    if 'ship_class' in df.columns:
        df['ship_class'] = df['ship_class'].map(ship_map).fillna(0)
        
    # Encode anomaly type (new column encoding)
    anom_map = {'none': 0, 'gravitational': 1, 'magnetic': 2, 'solar_flare': 3}
    if 'anomaly_type' in df.columns:
        df['anomaly_type'] = df['anomaly_type'].map(anom_map).fillna(0)
        
    # Drop target if present
    if 'target' in df.columns:
        df = df.drop(columns=['target'])
        
    return df
"""
`;
  } else if (gameId === 'vizdoom_toy') {
    code = `def run(args, obs):
    # ViZDoom bot (defend_the_center). Return an action id:
    # "idle", "turn_left", "turn_right", or "attack".
    # The frame is an opaque JPEG (obs["frame"]) — a fast bot can't "see" it, so
    # play off the HUD + tick: sweep the arena and gun down the imps.
    legals = [str(a["id"]) for a in obs.get("legal_actions", [])]
    hud = obs.get("hud", {})
    tick = int(obs.get("tick", 0))

    # Out of ammo? keep turning to face the next target.
    if hud.get("ammo", 0) <= 0 and "turn_right" in legals:
        return "turn_right"
    # Sweep to acquire every few ticks, otherwise fire.
    if tick % 3 == 2 and "turn_right" in legals:
        return "turn_right"
    return "attack" if "attack" in legals else "idle"
`;
  } else {
    code = `def run(args, obs):
    # Default bot. Return a legal action ID.
    import random
    legals = [str(a["id"]) for a in obs.get("legal_actions", [])]
    return random.choice(legals) if legals else "idle"
`;
  }

  return {
    name: `helper_${n}`,
    description: 'What this tool computes, so the agent knows when to call it.',
    code: code,
    parameters: {},
    required: [],
  };
}

// Mirrors backend/modules/games/loadout.py `tool_name_error` — same rule, checked
// live in the editor so a bad name is flagged before Save.
const TOOL_NAME_RE = /^[A-Za-z_][A-Za-z0-9_.-]*$/;

function toolNameError(name: string, taken: string[]): string | null {
  if (!name) return 'tool name is empty';
  if (!TOOL_NAME_RE.test(name))
    return 'must start with a letter or _ and use only letters, digits, _ . -';
  if (name.startsWith('game.')) return 'the game.* namespace is reserved for built-in tools';
  if (taken.includes(name)) return `duplicate tool name "${name}"`;
  return null;
}

const PARAM_TYPES = ['string', 'number', 'boolean', 'object', 'array'];

/** Row editor for a tool's `parameters`/`required` — the argument schema the
 * MODEL fills in when calling the tool (`args` in `run(args, obs)`). Edits patch
 * only `type`/`description` so hand-authored extras (e.g. `enum`) survive. */
function ParamsEditor({
  tool,
  onChange,
}: {
  tool: ToolDef;
  onChange: (patch: Partial<ToolDef>) => void;
}) {
  const entries = Object.entries(tool.parameters) as [string, Record<string, unknown>][];

  const rename = (oldName: string, newName: string) => {
    const parameters: Record<string, unknown> = {};
    for (const [k, v] of entries) parameters[k === oldName ? newName : k] = v;
    onChange({
      parameters,
      required: tool.required.map((r) => (r === oldName ? newName : r)),
    });
  };

  const patchParam = (name: string, patch: Record<string, unknown>) => {
    const current = (tool.parameters[name] ?? {}) as Record<string, unknown>;
    onChange({ parameters: { ...tool.parameters, [name]: { ...current, ...patch } } });
  };

  const remove = (name: string) => {
    const parameters = { ...tool.parameters };
    delete parameters[name];
    onChange({ parameters, required: tool.required.filter((r) => r !== name) });
  };

  const setRequired = (name: string, on: boolean) => {
    const rest = tool.required.filter((r) => r !== name);
    onChange({ required: on ? [...rest, name] : rest });
  };

  const add = () => {
    let n = entries.length + 1;
    while (`arg_${n}` in tool.parameters) n += 1;
    patchParam(`arg_${n}`, { type: 'string', description: '' });
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
        <span style={labelStyle}>Arguments the model passes (args)</span>
        <button type="button" onClick={add} style={{ fontSize: '0.72rem' }}>
          + arg
        </button>
        {entries.length === 0 && (
          <span style={{ color: 'var(--text-dim)', fontSize: '0.72rem' }}>
            none — the model calls this tool bare
          </span>
        )}
      </div>
      {entries.map(([name, spec], i) => (
        <div key={i} style={{ display: 'flex', gap: '0.3rem', alignItems: 'center' }}>
          <input
            value={name}
            onChange={(e) => rename(name, e.target.value)}
            placeholder="arg name"
            style={{ fontFamily: 'monospace', flex: '0 0 8rem' }}
          />
          <select
            value={String(spec.type ?? 'string')}
            onChange={(e) => patchParam(name, { type: e.target.value })}
          >
            {PARAM_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <input
            value={String(spec.description ?? '')}
            onChange={(e) => patchParam(name, { description: e.target.value })}
            placeholder="what the model should pass here"
            style={{ flex: 1 }}
          />
          <label
            style={{ display: 'flex', alignItems: 'center', gap: '0.2rem', fontSize: '0.72rem' }}
          >
            <input
              type="checkbox"
              checked={tool.required.includes(name)}
              onChange={(e) => setRequired(name, e.target.checked)}
            />
            required
          </label>
          <button type="button" onClick={() => remove(name)}>
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}

/** Static explainer: how the loadout actually drives a turn. Content mirrors
 * backend/modules/games/policy.py — update both if the loop changes. */
function HarnessExplainer() {
  return (
    <details className="games-harness-help" style={{ fontSize: '0.8rem' }}>
      <summary style={{ cursor: 'pointer' }}>ℹ️ How the harness works</summary>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '0.35rem',
          padding: '0.4rem 0 0.2rem 1rem',
          color: 'var(--text-dim)',
        }}
      >
        <div>
          <strong>The loop.</strong> On your agent's turn the server sends an observation and the
          legal actions. Your <em>strategy context</em> goes into the model's system prompt, and
          every tool below is offered to it. The model may call your tools for up to{' '}
          <strong>6 rounds</strong> to analyze the position, then must commit a move with the
          built-in <code>game.chooseAction</code>. In a real match any failure quietly falls back to
          a random legal move — the Dry run below shows the failure instead.
        </div>
        <div>
          <strong>Multiple tools are encouraged.</strong> Every tool is advertised on every round
          and the model picks which (if any) to call — it can chain them, e.g. a scanner first, then
          a fork finder. The model won't know your intended order: teach it in the context
          (&quot;call X first, then Y&quot;).
        </div>
        <div>
          <strong>The contract.</strong> Each tool is Python defining <code>run(args, obs)</code>:{' '}
          <code>args</code> = the arguments the model passed, <code>obs</code> = this seat's
          observation. Return anything JSON-serializable; raising shows the model{' '}
          <code>{'{"error": ...}'}</code>. A tool that doesn't compile is simply absent in a match —
          Save reports it here.
        </div>
        <div>
          <strong>Arguments.</strong> The &quot;args&quot; rows declare what the model fills in when
          calling (name, type, description; <em>required</em> makes it mandatory). No rows = the
          tool is called bare and should read everything from <code>obs</code>.
        </div>
        <div>
          <strong>The model.</strong> Each harness can bring its own model (part of the skill — the
          ladder records it), or borrow the agent module's configured model (&quot;agent
          default&quot;).
        </div>
      </div>
    </details>
  );
}

const GAME_SPECS: Record<
  string,
  {
    title: string;
    description: string;
    obsExample: string;
    returnsExample: string;
  }
> = {
  tictactoe: {
    title: 'Tic-Tac-Toe',
    description:
      'A 3x3 board game where players alternate turns to place X or O. Three in a row wins.',
    obsExample: `{
  "board": [null, "X", "O", null, null, null, null, null, null], // 9 cells top-left to bottom-right
  "game": "tictactoe",
  "seat": 0, // 0 or 1
  "legal_actions": [{"id": "0"}, {"id": "3"}, {"id": "4"}, {"id": "5"}, {"id": "6"}, {"id": "7"}, {"id": "8"}]
}`,
    returnsExample: `A string "0" to "8" representing the index of the cell to claim.
Example: return "4" (to mark the center cell)`,
  },
  connect_four: {
    title: 'Connect Four',
    description:
      'Drop discs into column slots. Get four of your discs in a row (vertical, horizontal, diagonal) to win.',
    obsExample: `{
  "board": [
    [null, null, null, null, null, null], // col 0 cells (bottom to top)
    ["X", null, null, null, null, null],  // col 1 cells
    [null, null, null, null, null, null],
    [null, null, null, null, null, null],
    [null, null, null, null, null, null],
    [null, null, null, null, null, null],
    [null, null, null, null, null, null]
  ],
  "game": "connect_four",
  "seat": 0,
  "legal_actions": [{"id": "0"}, {"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"}, {"id": "5"}, {"id": "6"}]
}`,
    returnsExample: `A string "0" to "6" representing the column index to drop your disc into.
Example: return "3"`,
  },
  holdem: {
    title: "Texas Hold'em",
    description:
      "Heads-up No-Limit Texas Hold'em. Stacks of 100, blinds 1/2. The goal is to maximize your chip returns.",
    obsExample: `{
  "me": {
    "hand": ["Ah", "Ks"], // rank: A, K, Q, J, Ten, 9-2. suit: h, s, d, c
    "chips": 80,
    "bet": 20
  },
  "opponent": {
    "chips": 90,
    "bet": 10
  },
  "community": ["Qc", "Jh", "9d"], // flop cards (empty pre-flop)
  "pot": 30,
  "street": "flop", // preflop, flop, turn, river
  "game": "holdem",
  "seat": 0,
  "legal_actions": [{"id": "fold"}, {"id": "call"}, {"id": "raise_min"}]
}`,
    returnsExample: `One of the legal action IDs (refer to 'legal_actions' list in obs):
- "fold": concede the pot
- "check": pass action
- "call": match the opponent's bet
- "raise_min", "raise_pot", "all_in": raise by corresponding amounts

Example: return "call"`,
  },
  rag_race: {
    title: 'RAG Race',
    description:
      'Simultaneous duel. Scan a set of fictional documents inside the observation and submit answers for questions.',
    obsExample: `{
  "corpus": [
    "The Snark was a Boojum, which lived in the forest of Gloom.",
    "Elmo found the red ruby at noon under the oak tree."
  ],
  "questions": [
    {"id": "q1", "text": "What was the Snark?"},
    {"id": "q2", "text": "When did Elmo find the ruby?"}
  ],
  "game": "rag_race",
  "legal_actions": [{"id": "submit"}]
}`,
    returnsExample: `A dictionary mapping question IDs to string answers.
Example:
return {
  "q1": "a Boojum",
  "q2": "noon"
}`,
  },
  arena: {
    title: 'Arena Bot Grid Duel',
    description:
      'Grid resource race. Alternate turns or move simultaneously to collect pellets and tag your opponent.',
    obsExample: `{
  "me": [3, 4], // x, y position
  "opponent": [5, 4],
  "opponent_prev": [5, 5],
  "pellets": [[1, 2], [7, 8], [3, 4]], // pellet coordinates
  "grid": 9, // grid dimension (9x9)
  "my_score": 5,
  "opponent_score": 3,
  "game": "arena",
  "legal_actions": [{"id": "up"}, {"id": "down"}, {"id": "left"}, {"id": "right"}, {"id": "stay"}]
}`,
    returnsExample: `A string representing the direction to move:
"up", "down", "left", "right", or "stay".

Example: return "up"`,
  },
  fighter: {
    title: 'Fighter 2D Battle',
    description: 'Tick-based 2D fighting game. Predict, block, and strike your opponent.',
    obsExample: `{
  "me": {
    "x": 2.5,
    "y": 0.0,
    "hp": 90,
    "meter": 25,
    "stun": 0
  },
  "opponent": {
    "x": 4.0,
    "y": 0.0,
    "hp": 100,
    "meter": 10,
    "stun": 0
  },
  "game": "fighter",
  "legal_actions": [{"id": "left"}, {"id": "right"}, {"id": "jump"}, {"id": "block"}, {"id": "light"}, {"id": "heavy"}, {"id": "special"}, {"id": "idle"}]
}`,
    returnsExample: `A string representing the tick action:
"left", "right", "jump", "block", "light", "heavy", "special", or "idle".

Example: return "light"`,
  },
  bug_hunt: {
    title: 'Bug Hunt (SWE-bench)',
    description: 'Locate code defects in a python workspace and submit a working fix.',
    obsExample: `{
  "workspace": "/tmp/bug_hunt_workspace",
  "issues": ["Test failing: test_math.py::test_addition"],
  "game": "bug_hunt",
  "legal_actions": [{"id": "submit"}]
}`,
    returnsExample: `A dictionary containing your proposed file modifications/patches.
Normally driven by the TaskAgent tool loops.`,
  },
  code_golf: {
    title: 'Code Golf',
    description:
      'Simultaneous coding duel. Solve the requested challenge using the minimum code size in bytes.',
    obsExample: `{
  "problem": "Write a function 'add(a, b)' returning the sum.",
  "signature": "def add(a, b):",
  "game": "code_golf",
  "legal_actions": [{"id": "submit"}]
}`,
    returnsExample: `A string containing the full Python script/function.
Example: return "def add(a,b):\\n return a+b"`,
  },
  test_duel: {
    title: 'Test Duel',
    description:
      "Simultaneous coding duel. Submit a valid implementation, then write tests to break the opponent's code.",
    obsExample: `{
  "specification": "Create an exponentiation function 'pow(base, exp)'.",
  "phase": "implement", // or "test"
  "game": "test_duel",
  "legal_actions": [{"id": "submit"}]
}`,
    returnsExample: `A string of implementation or test suite code.
Example: return "def pow(b, e):\\n return b ** e"`,
  },
  town: {
    title: 'AgentTown Sim',
    description:
      'Sims-style social agent simulation. Manage energy, wealth, resting, working, and social interactions.',
    obsExample: `{
  "you": {
    "name": "BakerBob",
    "energy": 75,
    "wealth": 15,
    "job": "Baker",
    "job_site": "bakery",
    "place": "bakery"
  },
  "places": ["fountain", "bakery", "gym", "tavern", "residential_zone"],
  "occupants": [{"name": "FisherBob", "place": "bakery"}],
  "phase": "afternoon",
  "legal_actions": [{"id": "act"}]
}`,
    returnsExample: `A dictionary action with an action keyword and parameters:
- To move: {"action": "move", "place": "gym"}
- To work/workout/rest/eat: {"action": "work"} | {"action": "rest"}
- To speak: {"action": "say", "text": "Hello!"}

Example: return {"action": "move", "place": "gym"}`,
  },
  tabular_fe: {
    title: 'Tabular Feature Engineering',
    description:
      'Simultaneous coding duel. Write a transform(df) function that cleans, encodes, and transforms a dataset to maximize predictive performance (ROC-AUC) of a model trained on the features.',
    obsExample: `{
  "task_name": "Space Mining Expedition Success",
  "description": "Predict whether a deep space asteroid-mining expedition will succeed...",
  "metric": "roc_auc",
  "data_samples": [
    {
      "distance": 320.5,
      "fuel_density": 1.2,
      "crew_experience": "rookie",
      "ship_class": "light_freighter",
      "anomaly_type": "none",
      "target": 0
    }
  ],
  "starter_code": "def transform(df): ...",
  "game": "tabular_fe",
  "legal_actions": [{"id": "submit"}]
}`,
    returnsExample: `A string containing the full Python script/function.
Example:
return """import pandas as pd
import numpy as np

def transform(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # clean, encode, and return numeric-only DataFrame
    return df
"""`,
  },
  vizdoom_toy: {
    title: 'ViZDoom',
    description:
      'Real native-Doom score race (defend_the_center): each seat holds the center of its own arena and guns down the imps closing in. Higher score (kills) when the clock runs out wins. The two marines never share a map — it is a race, not a face-to-face duel.',
    obsExample: `{
  "game": "vizdoom_toy",
  "seat": 0,
  "frame": "data:image/jpeg;base64,...", // your 160x120 first-person view (opaque to a fast bot)
  "hud": {"health": 100, "ammo": 26, "score": 3},
  "tick": 42,
  "max_ticks": 200,
  "legal_actions": [{"id": "idle"}, {"id": "turn_left"}, {"id": "turn_right"}, {"id": "attack"}]
}`,
    returnsExample: `One of the legal action IDs (refer to 'legal_actions' list in obs):
- "turn_left": rotate left
- "turn_right": rotate right
- "attack": fire your weapon
- "idle": do nothing

Example: return "attack"`,
  },
};

function GameReference({ gameId }: { gameId: string }) {
  const spec = GAME_SPECS[gameId] || GAME_SPECS['tictactoe'];

  return (
    <details className="games-spec-help" style={{ fontSize: '0.8rem' }}>
      <summary style={{ cursor: 'pointer', color: 'var(--accent, #6ea8fe)' }}>
        🎮 View Game Spec Reference ({spec.title})
      </summary>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '0.5rem',
          padding: '0.5rem 0 0.2rem 1rem',
          color: 'var(--text-dim)',
        }}
      >
        <div>
          <strong>Objective:</strong> {spec.description}
        </div>
        <div>
          <strong>
            Observation (<code>obs</code>) object structure:
          </strong>
          <pre
            style={{
              margin: '0.25rem 0',
              padding: '0.4rem',
              backgroundColor: 'var(--panel-2, rgba(255, 255, 255, 0.04))',
              border: '1px solid var(--border, #33343a)',
              borderRadius: '4px',
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: '0.72rem',
              overflowX: 'auto',
              maxHeight: '12rem',
              color: 'var(--text)',
            }}
          >
            {spec.obsExample}
          </pre>
        </div>
        <div>
          <strong>What your bot should return:</strong>
          <pre
            style={{
              margin: '0.25rem 0',
              padding: '0.4rem',
              backgroundColor: 'var(--panel-2, rgba(255, 255, 255, 0.04))',
              border: '1px solid var(--border, #33343a)',
              borderRadius: '4px',
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: '0.72rem',
              whiteSpace: 'pre-wrap',
              color: 'var(--text)',
            }}
          >
            {spec.returnsExample}
          </pre>
        </div>
      </div>
    </details>
  );
}

/** Which model drives this harness — part of the loadout, so part of the skill.
 * API keys go into the node's key store write-only; only names come back. */
function ModelSection({
  model,
  onChange,
}: {
  model: ModelConfig | null;
  onChange: (m: ModelConfig | null) => void;
}) {
  const [keyNames, setKeyNames] = useState<string[]>([]);
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyValue, setNewKeyValue] = useState('');
  const [note, setNote] = useState('');

  const loadKeys = useCallback(() => {
    apiGet<{ names: string[] }>('/games/keys')
      .then((r) => setKeyNames(r.names))
      .catch(() => setKeyNames([]));
  }, []);
  useEffect(() => loadKeys(), [loadKeys]);

  const addKey = async () => {
    if (!newKeyName || !newKeyValue) return;
    await apiPut(`/games/keys/${encodeURIComponent(newKeyName)}`, { value: newKeyValue });
    setNote(`key "${newKeyName}" stored on this node (write-only)`);
    setNewKeyName('');
    setNewKeyValue('');
    loadKeys();
  };

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: '4px',
        padding: '0.5rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.35rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <strong>Model</strong>
        <select
          value={model?.provider ?? 'agent'}
          onChange={(e) => {
            const p = e.target.value;
            if (p === 'agent') onChange(null);
            else
              onChange({
                provider: p as ModelConfig['provider'],
                model: model?.model ?? '',
                endpoint: null,
                api_key_name: model?.api_key_name ?? null,
              });
          }}
        >
          <option value="agent">agent default (node's local model)</option>
          <option value="ollama">Ollama</option>
          <option value="openai">OpenAI-compatible</option>
          <option value="anthropic">Anthropic</option>
        </select>
        {model && (
          <>
            <input
              value={model.model}
              onChange={(e) => onChange({ ...model, model: e.target.value })}
              placeholder={model.provider === 'anthropic' ? 'claude-sonnet-5' : 'model name'}
              style={{ fontFamily: 'monospace', flex: '0 0 14rem' }}
            />
            <input
              value={model.endpoint ?? ''}
              onChange={(e) => onChange({ ...model, endpoint: e.target.value || null })}
              placeholder="endpoint (default)"
              style={{ fontFamily: 'monospace', flex: '0 0 12rem' }}
            />
            {model.provider !== 'ollama' && (
              <select
                value={model.api_key_name ?? ''}
                onChange={(e) => onChange({ ...model, api_key_name: e.target.value || null })}
              >
                <option value="">no API key</option>
                {keyNames.map((n) => (
                  <option key={n} value={n}>
                    🔑 {n}
                  </option>
                ))}
              </select>
            )}
          </>
        )}
      </div>
      {model && model.provider !== 'ollama' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
          <span style={labelStyle}>Add key:</span>
          <input
            value={newKeyName}
            onChange={(e) => setNewKeyName(e.target.value)}
            placeholder="key name"
            style={{ flex: '0 0 8rem' }}
          />
          <input
            value={newKeyValue}
            onChange={(e) => setNewKeyValue(e.target.value)}
            placeholder="paste key (stored node-side, never shown again)"
            type="password"
            style={{ flex: 1, minWidth: '10rem' }}
          />
          <button type="button" onClick={() => void addKey()}>
            Store
          </button>
          <span style={{ color: 'var(--text-dim)', fontSize: '0.72rem' }}>{note}</span>
        </div>
      )}
    </div>
  );
}

export function LoadoutPanel() {
  const [gameId, setGameId] = useState('tictactoe');
  const [games, setGames] = useState(DEFAULT_GAMES);
  const [loadout, setLoadout] = useState<LoadoutModel | null>(null);
  const [sampleObs, setSampleObs] = useState(
    '{"board": [null,null,null,null,null,null,null,null,null]}',
  );
  const [results, setResults] = useState<Record<number, string>>({});
  const [status, setStatus] = useState('');
  // Tool code opened as an editor buffer, keyed by `${gameId}:${tool name}` so the
  // link survives list reorders (delete/add) but not renames.
  const [editorUris, setEditorUris] = useState<Record<string, string>>({});
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [stats, setStats] = useState<VersionStats>({});
  const [versionLabel, setVersionLabel] = useState('');
  // Per-tool problems from the last Save (`/games/loadout/validate`): a broken
  // tool is silently absent in a live match, so surface it here instead.
  const [diagnostics, setDiagnostics] = useState<Record<string, string>>({});
  // Which tool cards are expanded (index-aligned with loadout.tools; adjusted on
  // add/delete). Loaded tools start collapsed; new tools open; a Save that finds
  // problems force-opens the offenders.
  const [openTools, setOpenTools] = useState<boolean[]>([]);

  const loadVersions = useCallback(() => {
    apiGet<{ versions: VersionInfo[]; stats: VersionStats }>(`/games/loadout/${gameId}/versions`)
      .then((r) => {
        setVersions(r.versions);
        setStats(r.stats);
      })
      .catch(() => setVersions([]));
  }, [gameId]);
  useEffect(() => loadVersions(), [loadVersions]);

  useEffect(() => {
    // Catalog games + the AgentTown persona (the town isn't a table game, but its
    // resident's personality is this loadout's context) + the `default` fallback.
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
    setDiagnostics({});
    apiGet<LoadoutModel>(`/games/loadout/${gameId}`)
      .then((l) => {
        setLoadout(l);
        setOpenTools(l.tools.map(() => false));
        setStatus('');
      })
      .catch((e) => setStatus(String(e)));

    const spec = GAME_SPECS[gameId];
    if (spec) {
      setSampleObs(spec.obsExample);
    }
  }, [gameId]);

  if (!loadout) {
    return <div style={{ padding: '0.6rem', fontSize: '0.85rem' }}>{status || 'loading…'}</div>;
  }

  const update = (patch: Partial<LoadoutModel>) => setLoadout({ ...loadout, ...patch });
  const updateTool = (i: number, patch: Partial<ToolDef>) => {
    const oldName = loadout.tools[i]?.name;
    const newName = patch.name;
    if (newName !== undefined && oldName !== undefined && oldName !== newName) {
      const oldKey = `${gameId}:${oldName}`;
      const newKey = `${gameId}:${newName}`;
      if (editorUris[oldKey]) {
        setEditorUris((prev) => {
          const next = { ...prev };
          next[newKey] = next[oldKey];
          delete next[oldKey];
          return next;
        });
      }
    }
    update({ tools: loadout.tools.map((t, j) => (j === i ? { ...t, ...patch } : t)) });
  };

  const save = async () => {
    setStatus('saving…');
    try {
      await apiPut(`/games/loadout/${gameId}`, { ...loadout, game_id: gameId });
      // Save never blocks on problems (WIP harnesses are normal; matches degrade
      // gracefully), but the diagnostics land next to each tool.
      let suffix = '';
      try {
        const v = await apiPost<{
          ok: boolean;
          tools: { name: string; ok: boolean; error: string | null }[];
        }>('/games/loadout/validate', { ...loadout, game_id: gameId });
        const bad: Record<string, string> = {};
        for (const t of v.tools) if (!t.ok && t.error) bad[t.name] = t.error;
        setDiagnostics(bad);
        // A collapsed card hides its problem — force the offenders open.
        setOpenTools((prev) => loadout.tools.map((t, i) => prev[i] || t.name in bad));
        const n = Object.keys(bad).length;
        if (n > 0) suffix = ` — ${n} tool${n > 1 ? 's have' : ' has'} problems`;
      } catch {
        setDiagnostics({});
      }
      setStatus(`saved ✓${suffix}`);
      loadVersions();
    } catch (e) {
      setStatus(String(e));
    }
  };

  const saveAsVersion = async () => {
    setStatus('branching…');
    try {
      const r = await apiPost<{ version_id: string }>(`/games/loadout/${gameId}/versions`, {
        label: versionLabel,
        loadout: { ...loadout, game_id: gameId },
      });
      setStatus(`saved as ${versionLabel || r.version_id} ✓`);
      setVersionLabel('');
      loadVersions();
    } catch (e) {
      setStatus(String(e));
    }
  };

  const activate = async (versionId: string) => {
    await apiPut(`/games/loadout/${gameId}/active`, { version_id: versionId });
    const l = await apiGet<LoadoutModel>(`/games/loadout/${gameId}`);
    setLoadout(l);
    loadVersions();
  };

  const removeVersion = async (versionId: string) => {
    await apiDelete(`/games/loadout/${gameId}/versions/${versionId}`);
    const l = await apiGet<LoadoutModel>(`/games/loadout/${gameId}`);
    setLoadout(l);
    loadVersions();
  };

  const active = versions.find((v) => v.active);

  // Open a tool's code as a real Python buffer in the editor module (syntax
  // highlighting, LSP), then pull the edited content back into the loadout.
  const editInEditor = async (i: number) => {
    const svc = editor();
    if (!svc) return;
    const t = loadout.tools[i];
    const uri = await svc.openBufferFromContent({
      content: t.code,
      language: 'python',
      title: `harness · ${gameId} · ${t.name}`,
    });
    setEditorUris((prev) => ({ ...prev, [`${gameId}:${t.name}`]: uri }));
  };

  const pullFromEditor = async (i: number) => {
    const svc = editor();
    if (!svc) return;
    const t = loadout.tools[i];
    const uri = editorUris[`${gameId}:${t.name}`];
    if (!uri) return;
    const content = await svc.getBufferContent(uri);
    if (content !== null) {
      updateTool(i, { code: content });
      setResults({ ...results, [i]: 'pulled from editor — Save to persist' });
    } else {
      setResults({ ...results, [i]: 'editor buffer is gone' });
    }
  };

  const test = async (i: number) => {
    let obs: unknown = {};
    try {
      obs = JSON.parse(sampleObs);
    } catch {
      setResults({ ...results, [i]: 'sample observation is not valid JSON' });
      return;
    }
    try {
      const r = await apiPost<{ ok: boolean; result: unknown; error: string | null }>(
        '/games/test-tool',
        { code: loadout.tools[i].code, args: {}, obs },
      );
      setResults({ ...results, [i]: r.ok ? `→ ${JSON.stringify(r.result)}` : `error: ${r.error}` });
    } catch (e) {
      setResults({ ...results, [i]: String(e) });
    }
  };

  return (
    <div
      style={{
        padding: '0.6rem',
        fontSize: '0.85rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.6rem',
        overflow: 'auto',
        height: '100%',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <span>Harness for</span>
        <select value={gameId} onChange={(e) => setGameId(e.target.value)}>
          {games.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <button type="button" onClick={save}>
          Save
        </button>
        <span style={{ color: 'var(--text-dim)' }}>{status}</span>
      </div>

      <HarnessExplainer />
      <GameReference gameId={gameId} />

      <label>
        <span style={labelStyle}>Strategy context (injected into the agent's system prompt)</span>
        <CodeEditor
          value={loadout.context}
          onChange={(val) => update({ context: val })}
          language="markdown"
          placeholder="e.g. Prefer the center, then corners. Block the opponent's two-in-a-row."
          minHeight="3.5rem"
        />
      </label>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <strong>Custom tools</strong>
        <button
          type="button"
          onClick={() => {
            update({ tools: [...loadout.tools, newTool(loadout.tools.length + 1, gameId)] });
            setOpenTools((prev) => [...prev, true]);
          }}
        >
          + Add tool
        </button>
      </div>

      {loadout.tools.map((t, i) => {
        const nameError = toolNameError(
          t.name,
          loadout.tools.slice(0, i).map((x) => x.name),
        );
        const diagnostic = diagnostics[t.name];
        const problem = nameError ?? diagnostic;
        return (
          <details
            key={i}
            className="games-tool-card"
            open={openTools[i] ?? false}
            onToggle={(e) => {
              const open = (e.target as HTMLDetailsElement).open;
              setOpenTools((prev) => prev.map((o, j) => (j === i ? open : o)));
            }}
            style={{
              border: '1px solid var(--border)',
              borderRadius: '4px',
              padding: '0.15rem 0.5rem 0.35rem',
            }}
          >
            <summary>
              <code>{t.name || '(unnamed tool)'}</code>
              {t.description && <span className="games-tool-summary-desc">{t.description}</span>}
              <span
                className="games-tool-status"
                title={problem ?? 'no problems found at the last save'}
                style={problem ? { color: '#e5534b' } : { color: 'var(--text-dim)' }}
              >
                {problem ? '⚠' : '✓'}
              </span>
            </summary>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
              <div style={{ display: 'flex', gap: '0.4rem' }}>
                <input
                  value={t.name}
                  onChange={(e) => updateTool(i, { name: e.target.value })}
                  placeholder="tool_name"
                  style={{
                    fontFamily: 'monospace',
                    flex: '0 0 12rem',
                    ...(nameError ? { border: '1px solid #e5534b' } : {}),
                  }}
                />
                <input
                  value={t.description}
                  onChange={(e) => updateTool(i, { description: e.target.value })}
                  placeholder="description"
                  style={{ flex: 1 }}
                />
                <button
                  type="button"
                  onClick={() => {
                    update({ tools: loadout.tools.filter((_, j) => j !== i) });
                    setOpenTools((prev) => prev.filter((_, j) => j !== i));
                  }}
                >
                  ✕
                </button>
              </div>
              {nameError && (
                <div style={{ color: '#e5534b', fontSize: '0.72rem' }}>⚠ {nameError}</div>
              )}
              {!nameError && diagnostic && (
                <div style={{ color: '#e5534b', fontSize: '0.72rem' }}>⚠ {diagnostic}</div>
              )}
              <CodeEditor
                value={t.code}
                onChange={(val) => updateTool(i, { code: val })}
                language="python"
                minHeight="6rem"
              />
              <ParamsEditor tool={t} onChange={(patch) => updateTool(i, patch)} />
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <button type="button" onClick={() => test(i)}>
                  Test
                </button>
                {editor() && (
                  <>
                    <button type="button" onClick={() => void editInEditor(i)}>
                      Edit in editor ↗
                    </button>
                    {editorUris[`${gameId}:${t.name}`] && (
                      <button type="button" onClick={() => void pullFromEditor(i)}>
                        ↙ Pull
                      </button>
                    )}
                  </>
                )}
                <code style={{ color: 'var(--text-dim)', fontSize: '0.72rem' }}>
                  {results[i] ?? ''}
                </code>
              </div>
            </div>
          </details>
        );
      })}

      <DryRunSection
        gameId={gameId}
        loadout={loadout}
        engineGames={games.filter((g) => g.id !== 'town' && g.id !== 'default')}
      />

      {/* Everything that isn't day-to-day authoring lives behind one fold. */}
      <details className="games-advanced">
        <summary>
          Advanced —{' '}
          <span style={{ color: 'var(--text-dim)' }}>
            model:{' '}
            {loadout.model?.model
              ? `${loadout.model.provider} · ${loadout.model.model}`
              : 'agent default'}{' '}
            · version: {active?.label ?? 'v1 (unsaved)'}
          </span>
        </summary>
        <div
          style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', paddingTop: '0.4rem' }}
        >
          {/* Version bar — the harness progression loop: play, study, branch, requeue. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
            <span style={labelStyle}>Version</span>
            <select value={active?.id ?? ''} onChange={(e) => void activate(e.target.value)}>
              {versions.length === 0 && <option value="">v1 (unsaved)</option>}
              {versions.map((v) => {
                const s = stats[v.id];
                const record = s ? ` — ${s.win}W/${s.loss}L/${s.draw}D` : '';
                return (
                  <option key={v.id} value={v.id}>
                    {v.label}
                    {record}
                  </option>
                );
              })}
            </select>
            <input
              value={versionLabel}
              onChange={(e) => setVersionLabel(e.target.value)}
              placeholder="new version label"
              style={{ flex: '0 0 11rem' }}
            />
            <button type="button" onClick={() => void saveAsVersion()} title="Branch this harness">
              Save as new version
            </button>
            {active && versions.length > 1 && (
              <button
                type="button"
                onClick={() => void removeVersion(active.id)}
                title="Delete this version"
              >
                🗑
              </button>
            )}
            {active && stats[active.id] && (
              <span className="games-tier-chip" title="this version's record">
                {stats[active.id].win}W · {stats[active.id].loss}L · {stats[active.id].draw}D
              </span>
            )}
          </div>

          <ModelSection model={loadout.model} onChange={(m) => update({ model: m })} />

          <div>
            <span style={labelStyle}>Sample observation (JSON) — for testing tools</span>
            <CodeEditor
              value={sampleObs}
              onChange={(val) => setSampleObs(val)}
              language="json"
              minHeight="2.5rem"
            />
          </div>
        </div>
      </details>
    </div>
  );
}
