import { apiDelete, apiGet } from '../../api';
import { subscribeChannel } from '../../ws';

/** One labelled piece of the assembled prompt. Mirrors backend ContextBlock. */
export interface ContextBlock {
  kind: string;
  role: string;
  label: string;
  content: string;
  tokens: number;
  /** `content` was clipped for transport; `tokens` still counts the full text. */
  clipped: boolean;
  fullChars: number;
}

export interface ToolEntry {
  name: string;
  group: string;
  tokens: number;
}

export interface RoundSnapshot {
  round: number;
  blocks: ContextBlock[];
  tools: ToolEntry[];
  messageTokens: number;
  toolTokens: number;
  totalTokens: number;
  toolsSelected: number;
  toolBudget: number;
  toolsTruncated: boolean;
  activeGroups: string[];
}

export interface TurnSnapshot {
  turnId: string;
  agentId: string;
  model: string;
  provider: string;
  startedAt: number;
  rounds: RoundSnapshot[];
  /** Set when this turn was spawned by `agent.delegate` from another turn. */
  parentTurnId: string | null;
  agentName: string;
  /** Declared tool-group scope. null = unrestricted (only `main` is). */
  toolGroups: string[] | null;
  permissionMode: string | null;
  /** "local" — a real loop here, with rounds. "peer" — ran on another node. */
  kind: string;
  peerId: string | null;
  sentPrompt: string | null;
  /** False when counts aren't authoritative — estimated, or a family stand-in. */
  exact: boolean;
  tokenizerRepo: string | null;
  /** "model" | "setting" | "family" | "none" — how the tokenizer was chosen. */
  tokenizerSource: string;
  requestedNumCtx: number | null;
  modelContextLength: number | null;
  temperature: number | null;
  topP: number | null;
  maxTokens: number | null;
}


/** The loaded model's structure. Every dimension is optional: a field the metadata
 *  couldn't confirm stays null and is simply not drawn. */
export interface AttentionSpec {
  heads: number | null;
  kvHeads: number | null;
  headDim: number | null;
  /** headDim was computed as hidden/heads, not read — shown as an estimate. */
  headDimDerived: boolean;
  /** mha | gqa | mqa | unknown — derived from the head counts. */
  kind: string;
  groupRatio: number | null;
  slidingWindow: number | null;
  ropeTheta: number | null;
}

export interface FfnSpec {
  intermediateSize: number | null;
  activation: string | null;
  expansionRatio: number | null;
  /** Gated (SwiGLU/GeGLU) FFNs have two up-projections, not one. */
  gated: boolean | null;
}

export interface MoeSpec {
  experts: number | null;
  expertsPerToken: number;
  expertIntermediateSize: number | null;
  sharedExperts: number | null;
  activeFraction: number | null;
}

export interface ModelArchitecture {
  /** "ollama" (read off the loaded weights) | "huggingface" (repo config) | "none" */
  source: string;
  sourceDetail: string;
  model: string;
  family: string | null;
  parameterCount: number | null;
  layers: number | null;
  hiddenSize: number | null;
  vocabSize: number | null;
  contextLength: number | null;
  tiedEmbeddings: boolean | null;
  normType: string | null;
  attention: AttentionSpec | null;
  ffn: FfnSpec | null;
  moe: MoeSpec | null;
  notes: string[];
  error: string | null;
}

export interface ModelInfo {
  model: string;
  provider: string;
  contextLength: number | null;
  template: string | null;
  parameters: string | null;
  family: string | null;
  error: string | null;
}

/**
 * Captured turns, newest first, fed by the `interpretability` `/ws` channel and
 * back-filled from the API on open.
 *
 * Deliberately a plain external store rather than context/state: rounds arrive
 * mid-turn from a socket that outlives any one component, and the pane is a
 * non-singleton that may be open more than once.
 */
class InterpretabilityStore {
  private turns: TurnSnapshot[] = [];
  private listeners = new Set<() => void>();
  private started = false;
  private modelInfo: ModelInfo | null = null;
  private architecture: ModelArchitecture | null = null;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    this.start();
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): TurnSnapshot[] => this.turns;

  getModelInfo = (): ModelInfo | null => this.modelInfo;

  getArchitecture = (): ModelArchitecture | null => this.architecture;

  private emit(): void {
    for (const listener of this.listeners) listener();
  }

  /** Attach the socket + back-fill once, on first subscriber. */
  private start(): void {
    if (this.started) return;
    this.started = true;

    subscribeChannel('interpretability', (msg) => {
      if (msg.event === 'round') {
        const data = msg.data as { turnId?: string; round?: RoundSnapshot } | undefined;
        if (!data?.turnId || !data.round) return;
        this.applyRound(data.turnId, data.round);
        return;
      }
      if (msg.event === 'peer') {
        // An `agent.ask_peer` handoff. Arrives whole (it has no rounds to stream)
        // and is inserted directly rather than going through applyRound.
        const data = msg.data as { turn?: TurnSnapshot } | undefined;
        if (!data?.turn) return;
        if (!this.turns.some((t) => t.turnId === data.turn!.turnId)) {
          this.turns = [data.turn, ...this.turns];
          this.emit();
        }
      }
    });

    void this.refresh();
  }

  /**
   * Merge a live round into its turn. A round can arrive before the turn's own
   * metadata (the socket beats the back-fill), so an unknown turnId gets a
   * placeholder that the next refresh fills in — dropping it would lose round 0,
   * which is the one carrying the fully assembled prompt.
   */
  private applyRound(turnId: string, round: RoundSnapshot): void {
    const existing = this.turns.find((t) => t.turnId === turnId);
    if (existing) {
      const rounds = existing.rounds.filter((r) => r.round !== round.round);
      const merged = { ...existing, rounds: [...rounds, round].sort((a, b) => a.round - b.round) };
      this.turns = this.turns.map((t) => (t.turnId === turnId ? merged : t));
    } else {
      this.turns = [
        {
          turnId,
          agentId: 'main',
          model: '',
          provider: '',
          startedAt: Date.now() / 1000,
          rounds: [round],
          parentTurnId: null,
          agentName: '',
          toolGroups: null,
          permissionMode: null,
          kind: 'local',
          peerId: null,
          sentPrompt: null,
          exact: true,
          tokenizerRepo: null,
          tokenizerSource: 'none',
          requestedNumCtx: null,
          modelContextLength: null,
          temperature: null,
          topP: null,
          maxTokens: null,
        },
        ...this.turns,
      ];
      // Pull the real metadata (model, tokenizer, sampling params) for the placeholder.
      void this.refresh();
    }
    this.emit();
  }

  /** Re-read captured turns + model info from the backend. */
  refresh = async (): Promise<void> => {
    try {
      // The architecture rides along with the same refresh so the diagram always
      // describes the model the captured turns actually ran on.
      const [turnsRes, model, architecture] = await Promise.all([
        apiGet<{ turns: TurnSnapshot[] }>('/interpretability/turns'),
        apiGet<ModelInfo>('/interpretability/model').catch(() => null),
        apiGet<ModelArchitecture>('/interpretability/architecture').catch(() => null),
      ]);
      // Server is authoritative for metadata; keep any live rounds that beat it here.
      this.turns = turnsRes.turns.map((turn) => {
        const local = this.turns.find((t) => t.turnId === turn.turnId);
        return local && local.rounds.length > turn.rounds.length
          ? { ...turn, rounds: local.rounds }
          : turn;
      });
      this.modelInfo = model;
      this.architecture = architecture;
      this.emit();
    } catch {
      // Backend down or restarting — keep whatever the socket has given us.
    }
  };

  clear = async (): Promise<void> => {
    try {
      await apiDelete('/interpretability/turns');
    } catch {
      // Clearing is best-effort; drop the local view regardless.
    }
    this.turns = [];
    this.emit();
  };
}

export const interpretabilityStore = new InterpretabilityStore();

/** A turn plus its delegated children, flattened for rendering with a depth. */
export interface TurnTreeRow {
  turn: TurnSnapshot;
  depth: number;
}

/**
 * Arrange captured turns into their handoff tree, depth-first, newest root first.
 *
 * `main` delegates one level by design (specialists have `can_delegate: false`), so
 * depth is 0 or 1 in practice — but this recurses anyway rather than hardcoding
 * that, because plugin-contributed agents set their own `can_delegate`.
 *
 * An orphan — a child whose parent has already been evicted from the 25-turn ring —
 * is promoted to a root rather than dropped. Losing a captured turn because its
 * parent aged out would be worse than showing it slightly out of context.
 */
export function buildTurnTree(turns: TurnSnapshot[]): TurnTreeRow[] {
  const byId = new Map(turns.map((t) => [t.turnId, t]));
  const children = new Map<string, TurnSnapshot[]>();
  const roots: TurnSnapshot[] = [];

  for (const turn of turns) {
    const parent = turn.parentTurnId;
    if (parent && byId.has(parent)) {
      const list = children.get(parent) ?? [];
      list.push(turn);
      children.set(parent, list);
    } else {
      roots.push(turn);
    }
  }

  const rows: TurnTreeRow[] = [];
  const seen = new Set<string>();
  const walk = (turn: TurnSnapshot, depth: number): void => {
    // Guards against a cycle in malformed data; without it a self-parenting turn
    // would recurse until the stack blows.
    if (seen.has(turn.turnId)) return;
    seen.add(turn.turnId);
    rows.push({ turn, depth });
    const kids = (children.get(turn.turnId) ?? []).sort((a, b) => a.startedAt - b.startedAt);
    for (const kid of kids) walk(kid, depth + 1);
  };
  for (const root of roots) walk(root, 0);
  return rows;
}

/** Distinct agents across the captured turns, for the filter control. */
export function agentsIn(turns: TurnSnapshot[]): { id: string; name: string }[] {
  const seen = new Map<string, string>();
  for (const t of turns) if (!seen.has(t.agentId)) seen.set(t.agentId, t.agentName || t.agentId);
  return [...seen.entries()].map(([id, name]) => ({ id, name }));
}

/** Colour/grouping key for a block kind, so the composition bar stays legible. */
export const BLOCK_KINDS = [
  'system',
  'guides',
  'history',
  'editor',
  'user',
  'assistant',
  'tool_result',
  'nudge',
] as const;
