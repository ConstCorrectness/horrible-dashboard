/**
 * **Per-game tutorial tracks** — "work your way up to a harness".
 *
 * A track is an ordered list of steps that walks a new player from watching a match to
 * a real, working agent. The progression differs by decision class (see game-identity):
 *
 * - **policy games** (bot(obs) → action): watch → read the obs → a reflex bot → a
 *   heuristic bot that reads state. ViZDoom Duel is the flagship.
 * - **reasoner games** (LLM harness): read the task obs → the default agent → add a
 *   tool / retrieval.
 *
 * Each step carries starter `code` for the relevant editor slot (`target`: the bot tool
 * for policy games, `agent_code` for reasoner games). A `bot`-target step can be tested
 * against a sample observation (via `testTool`) so the player sees it pick a legal move
 * before ever starting a match — the validation half of the loop. See the BootcampSection
 * panel and docs/modules/games.mdx.
 */

import type { DecisionClass } from './games-api';

export interface TutorialStep {
  id: string;
  title: string;
  /** One or two sentences: what this step teaches and why. */
  goal: string;
  /** Starter code the "Load into editor" button drops in. */
  code: string;
  /** Which editor slot the code fills — the bot tool, or `agent_code`. */
  target: 'bot' | 'agent';
}

export interface TutorialTrack {
  title: string;
  intro: string;
  steps: TutorialStep[];
}

// ---- flagship: ViZDoom Duel -------------------------------------------------

const VIZDOOM_STEP1 = `def run(args, obs):
    # Step 1 — attack and sweep. The frame is an opaque JPEG (no vision), so just
    # keep firing and turn a little each tick so you're never a standing target.
    legal = [a["id"] for a in obs.get("legal_actions", [])]
    tick = int(obs.get("tick", 0))
    if "attack" in legal and tick % 2 == 0:
        return "attack"
    return "turn_right" if "turn_right" in legal else legal[0]
`;

const VIZDOOM_STEP2 = `def run(args, obs):
    # Step 2 — read the HUD. Only fire when you have ammo; otherwise close the
    # distance and hunt a new angle. obs["hud"] carries health / ammo / score.
    legal = [a["id"] for a in obs.get("legal_actions", [])]
    hud = obs.get("hud") or {}
    ammo = hud.get("ammo", 0)
    tick = int(obs.get("tick", 0))
    if ammo > 0 and "attack" in legal and tick % 2 == 0:
        return "attack"
    if tick % 4 < 2 and "move_forward" in legal:
        return "move_forward"
    return "turn_right" if "turn_right" in legal else legal[0]
`;

const VIZDOOM_TRACK: TutorialTrack = {
  title: 'ViZDoom Duel bootcamp',
  intro:
    'ViZDoom Duel is a real-time policy game: every tick you get an observation and return one legal action. No model — a fast Python function is the whole agent. Work up from a reflex to a HUD-aware brawler.',
  steps: [
    {
      id: 'attack',
      title: 'Attack and sweep',
      goal: 'The simplest bot that does something: fire on the beat and turn so you keep weaving. Load it, then test it on a sample tick — it should return a legal action.',
      code: VIZDOOM_STEP1,
      target: 'bot',
    },
    {
      id: 'hud',
      title: 'Read the HUD',
      goal: 'Make the bot stateful: only fire when obs["hud"]["ammo"] is positive, otherwise reposition. This is the jump from reflex to reading the observation.',
      code: VIZDOOM_STEP2,
      target: 'bot',
    },
  ],
};

// ---- generic tracks by decision class --------------------------------------

const REFLEX_BOT = `def run(args, obs):
    # A one-line reflex: pick the first legal action. Build up from here — read
    # the observation fields and choose deliberately.
    return obs["legal_actions"][0]["id"]
`;

const HEURISTIC_BOT = `def run(args, obs):
    # A heuristic: look at the observation and choose. Inspect obs above to see
    # what fields this game gives you, then rank the legal actions yourself.
    legal = [a["id"] for a in obs.get("legal_actions", [])]
    # TODO: replace with real scoring using the observation.
    return legal[0]
`;

const GENERIC_POLICY_TRACK: TutorialTrack = {
  title: 'Bot bootcamp',
  intro:
    'This is a policy game: your agent is a bot(obs) → action function that runs every tick. No model needed — start with a reflex and add logic that reads the observation.',
  steps: [
    {
      id: 'reflex',
      title: 'A reflex bot',
      goal: 'The smallest thing that plays: return the first legal action. Load it and test it on a sample position.',
      code: REFLEX_BOT,
      target: 'bot',
    },
    {
      id: 'heuristic',
      title: 'Read the observation',
      goal: 'Use the observation above to score the legal actions and pick deliberately — the difference between random and a real policy.',
      code: HEURISTIC_BOT,
      target: 'bot',
    },
  ],
};

const DEFAULT_AGENT = `async def my_agent(obs, config):
    """Let your context + tools drive the model, then commit a legal move."""
    return await config.decide(obs)
`;

const RAG_AGENT = `async def my_agent(obs, config):
    """Ground the model on your library first, then decide."""
    docs = await config.retrieve(obs.get("query", ""), k=5)
    config.note(f"retrieved {len(docs)} docs")
    return await config.decide(obs)
`;

const GENERIC_REASONER_TRACK: TutorialTrack = {
  title: 'Harness bootcamp',
  intro:
    'This is a reasoner game: the observation is a task and an LLM decides. Your harness — the system prompt, tools, and model — is what you tune. Start with the default agent and add grounding.',
  steps: [
    {
      id: 'default',
      title: 'The default agent',
      goal: 'The baseline: your context + tools drive the model, which commits a legal move. Load it, open Context to write your system prompt.',
      code: DEFAULT_AGENT,
      target: 'agent',
    },
    {
      id: 'rag',
      title: 'Ground with retrieval',
      goal: 'Pull from your library before deciding — the retrieval half of RAG. Adapt the query to what the observation gives you.',
      code: RAG_AGENT,
      target: 'agent',
    },
  ],
};

const TRACKS: Record<string, TutorialTrack> = {
  vizdoom_duel: VIZDOOM_TRACK,
  vizdoom_toy: VIZDOOM_TRACK,
};

/** The tutorial track for a game — its own if it has one, else the generic track for
 * its decision class. */
export function tutorialFor(gameId: string, decisionClass: DecisionClass): TutorialTrack {
  return (
    TRACKS[gameId] ?? (decisionClass === 'reasoner' ? GENERIC_REASONER_TRACK : GENERIC_POLICY_TRACK)
  );
}
