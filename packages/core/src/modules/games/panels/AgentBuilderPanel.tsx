import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';

import { useSetting } from '../../../settings';
import { openDrawer } from '../client-drawer';
import {
  gradedStat,
  power as buildPower,
  readBuild,
  stats,
  STAT_LABEL,
  type StatKey,
} from '../agentBuild';
import { decisionClassBadge, decisionClassOf } from '../game-identity';
import {
  fetchGamesCatalog,
  fetchLoadoutTemplates,
  getAgentStarter,
  getLoadout,
  saveLoadout,
  validateLoadout,
  type GameCatalogEntry,
  type Loadout,
  type LoadoutTemplate,
  type MovePolicy,
} from '../games-api';
import { setGamesSection } from '../hub-section';
import { playVsOwnAgent } from '../matchmaking';
import { setActiveGame, useActiveGame } from '../selected-game';
import { BootcampSection } from './BootcampSection';
import { CodeEditor } from './CodeEditor';
import { HarnessPipeline } from './HarnessPipeline';
import { ObservationInspector } from './ObservationInspector';
import { ToolsEditor } from './ToolsEditor';
import { VsCeremony } from './VsCeremony';

/**
 * **Build your agent** — the games module's agent builder (was "Agent Harness").
 *
 * Your agent is code: a `my_agent(obs, config)` entrypoint (`agent_code`) that ranges
 * from a one-liner to a fully-harnessed RAG/memory rig. The same editor for every game;
 * only the starter and the graded stat change. The **loadout** panel on the right is a
 * live readout of your agent — which abilities it actually uses (Tools/RAG/Memory/Model,
 * detected from your code) and the resulting stats — so the build reflects the code, not
 * a set of separate switches. Empty agent_code = the default agent (your context + tools
 * drive the model). See docs/modules/games.mdx and backend agent_sdk.py.
 */

const card: CSSProperties = {
  background: 'var(--surface, #16171d)',
  border: '1px solid var(--border, #33343a)',
  borderRadius: 10,
};
const sectionLbl: CSSProperties = {
  fontFamily: 'var(--font-mono, monospace)',
  fontSize: '0.68rem',
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: 'var(--text-dim, #888)',
};
const btn: CSSProperties = {
  fontFamily: 'var(--font-mono, monospace)',
  fontSize: '0.78rem',
  padding: '0.5rem 0.9rem',
  borderRadius: 8,
  border: '1px solid var(--border, #33343a)',
  background: 'transparent',
  color: 'var(--text)',
  cursor: 'pointer',
};

export function AgentBuilderPanel() {
  const [games, setGames] = useState<GameCatalogEntry[]>([
    { id: 'tictactoe', name: 'Tic-Tac-Toe' },
  ]);
  // The active game is shared with the Games Library (see selected-game.ts), so
  // switching games there switches the harness here. Seed from it on mount; fall
  // back to Tic-Tac-Toe when nothing's been selected yet.
  const activeGame = useActiveGame();
  const [gameId, setGameId] = useState(activeGame ?? 'tictactoe');
  const [loadout, setLoadout] = useState<Loadout | null>(null);
  const [status, setStatus] = useState('');
  const [agentError, setAgentError] = useState<string | null>(null);
  const [showContext, setShowContext] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const [vsOpen, setVsOpen] = useState(false);
  const [templates, setTemplates] = useState<LoadoutTemplate[]>([]);

  useEffect(() => {
    fetchGamesCatalog()
      .then(setGames)
      .catch(() => {});
  }, []);

  // The shipped tool templates for this game — the editor's starting points for
  // `tools` (the backend has always served these; nothing consumed them before).
  useEffect(() => {
    let cancelled = false;
    fetchLoadoutTemplates(gameId)
      .then((t) => !cancelled && setTemplates(t))
      .catch(() => !cancelled && setTemplates([]));
    return () => {
      cancelled = true;
    };
  }, [gameId]);

  // Follow the shared selection: when the library (or another panel) switches the
  // active game, switch the harness to match.
  useEffect(() => {
    if (activeGame) setGameId(activeGame);
  }, [activeGame]);

  // Load the game's agent; seed the editor with the per-game starter if it's blank.
  useEffect(() => {
    let cancelled = false;
    setStatus('loading…');
    setAgentError(null);
    getLoadout(gameId)
      .then(async (lo) => {
        if (!lo.agent_code?.trim()) {
          try {
            lo = { ...lo, agent_code: (await getAgentStarter(gameId)).agent_code };
          } catch {
            // starter unavailable — leave blank (still valid: the default agent)
          }
        }
        if (!cancelled) {
          setLoadout(lo);
          setStatus('');
        }
      })
      .catch((e: Error) => !cancelled && setStatus(String(e.message || e)));
    return () => {
      cancelled = true;
    };
  }, [gameId]);

  const build = useMemo(() => (loadout ? readBuild(loadout) : null), [loadout]);
  const st = useMemo(() => (build ? stats(build) : null), [build]);
  const graded = gradedStat(gameId);
  const power = useMemo(() => (build ? buildPower(build) : 0), [build]);

  // The game's decision class + presentation, from the catalog (fallback: the id map).
  // A `policy` game is a bot(obs) mapping (no model); a `reasoner` game is the LLM
  // harness — the Build view branches on this so it never shows knobs that don't run.
  const entry = games.find((g) => g.id === gameId);
  const decisionClass = entry?.decision_class ?? decisionClassOf(gameId);
  const obsKind = entry?.obs_kind;
  const badge = decisionClassBadge(decisionClass);
  // The effective move policy for THIS game (per-game override → catalog default),
  // used to drive the pipeline strip. Mirrors PlaySection's resolution.
  const policyOverride = useSetting<string>(`games.policy.${gameId}`);
  const policy: MovePolicy = (policyOverride ??
    entry?.default_policy ??
    (decisionClass === 'reasoner' ? 'agent' : 'bot')) as MovePolicy;

  const patch = useCallback(
    (p: Partial<Loadout>) => setLoadout((lo) => (lo ? { ...lo, ...p } : lo)),
    [],
  );

  // Policy games decide with a bot tool named `<game>.bot` (or `bot`): a pure
  // `def run(args, obs)` that returns a legal action id — this is the "bot(obs)"
  // the code-first view edits, distinct from the reasoner path's `agent_code`.
  const botTool = loadout?.tools.find((t) => t.name === `${gameId}.bot` || t.name === 'bot');
  const setBotCode = useCallback(
    (code: string) => {
      setLoadout((lo) => {
        if (!lo) return lo;
        const name =
          lo.tools.find((t) => t.name === `${gameId}.bot` || t.name === 'bot')?.name ??
          `${gameId}.bot`;
        const has = lo.tools.some((t) => t.name === name);
        const tools = has
          ? lo.tools.map((t) => (t.name === name ? { ...t, code } : t))
          : [
              ...lo.tools,
              {
                name,
                description:
                  "Returns this tick's action id (bot policy — runs every tick, no model).",
                code,
                parameters: {},
                required: [],
              },
            ];
        return { ...lo, tools };
      });
    },
    [gameId],
  );

  // Load a shipped template's tool definitions as a starting point. Additive on
  // purpose: a template is a gift, not a reset — it appends the tools you don't
  // already have (matching by name, so re-applying is idempotent and never clobbers
  // a tool you've since edited) and only adopts its context when you have none.
  const applyTemplate = useCallback((t: LoadoutTemplate) => {
    setLoadout((lo) => {
      if (!lo) return lo;
      const have = new Set(lo.tools.map((x) => x.name));
      const added = t.loadout.tools.filter((x) => !have.has(x.name));
      return {
        ...lo,
        tools: [...lo.tools, ...added],
        context: lo.context.trim() ? lo.context : t.loadout.context,
      };
    });
    setShowTools(true);
    setStatus(`loaded template "${t.title}"`);
  }, []);

  // Validate + persist the active version. Returns true if the agent is saveable
  // (compiles); false surfaces the error and blocks lock-in.
  const save = useCallback(async (): Promise<boolean> => {
    if (!loadout) return false;
    setStatus('saving…');
    setAgentError(null);
    try {
      const v = await validateLoadout(loadout);
      if (v.agent_error) {
        setAgentError(v.agent_error);
        setStatus('agent has an error');
        return false;
      }
      await saveLoadout(gameId, loadout);
      setStatus('saved ✓');
      return true;
    } catch (e) {
      setStatus(String((e as Error).message || e));
      return false;
    }
  }, [loadout, gameId]);

  // Lock in: save, then open the VS ceremony (only if the agent actually compiles).
  const lockIn = useCallback(async () => {
    if (await save()) setVsOpen(true);
  }, [save]);

  // From the ceremony's "Enter arena": start a real self-play match (no sign-in
  // needed — it plays on this node), which switches this pane to the Game Board
  // section; also open the Games Log so the reasoning streams as the agent plays.
  const enterArena = useCallback(() => {
    setVsOpen(false);
    void playVsOwnAgent(gameId);
    openDrawer('log');
  }, [gameId]);

  if (!loadout || !build || !st) {
    return (
      <div style={{ padding: '0.8rem', fontSize: '0.85rem', color: 'var(--text-dim)' }}>
        {status || 'loading…'}
      </div>
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
        padding: 14,
        height: '100%',
        boxSizing: 'border-box',
        overflow: 'auto',
      }}
    >
      {/* Decision-class header + the harness pipeline + a live observation, so the
          build reads against reality: what your policy sees, what runs, what it returns. */}
      <div style={{ ...card, padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span
            style={{
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: '0.72rem',
              padding: '0.2rem 0.5rem',
              borderRadius: 999,
              border: '1px solid var(--border)',
              color: 'var(--text)',
            }}
          >
            {badge.icon} {badge.label} game
          </span>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
            {decisionClass === 'policy'
              ? 'Your agent is a bot(obs) → action mapping. An LLM is optional here.'
              : 'The LLM harness is the game: your system prompt, tools, and model.'}
          </span>
        </div>
        <HarnessPipeline policy={policy} />
        <ObservationInspector gameId={gameId} obsKind={obsKind} />
        <BootcampSection
          gameId={gameId}
          decisionClass={decisionClass}
          onLoadBot={setBotCode}
          onLoadAgent={(code) => patch({ agent_code: code })}
        />
      </div>

      <div
        style={{
          display: 'grid',
          // Two columns when there's room; stacks to one when the pane is narrow
          // (container-driven via auto-fit, no viewport media query needed).
          gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 300px), 1fr))',
          gap: 14,
          alignItems: 'start',
        }}
      >
        {/* Editor column */}
        <div style={{ ...card, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '0.5rem 0.7rem',
              borderBottom: '1px solid var(--border, #33343a)',
            }}
          >
            <button
              type="button"
              onClick={() => setGamesSection('play')}
              title="Back to Play"
              style={{ ...btn, padding: '0.2rem 0.5rem' }}
            >
              ← Play
            </button>
            <span style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '0.8rem' }}>
              {policy === 'bot' ? `${gameId}.bot` : 'my_agent.py'}
            </span>
            <span style={{ ...sectionLbl, color: 'var(--text-faint, #666)' }}>
              · {policy === 'bot' ? 'bot(obs) → action' : gameId}
            </span>
          </div>

          {policy === 'bot' ? (
            // Bot policy: the decision is a pure `def run(args, obs)` that returns a
            // legal action id every tick — no system prompt, no model. Context/model
            // knobs are hidden because they do nothing under the bot policy. (The
            // editor follows the chosen policy, so agent-policy games get the harness.)
            <>
              <div
                style={{
                  padding: '0.5rem 0.7rem',
                  fontSize: '0.72rem',
                  color: 'var(--text-dim)',
                }}
              >
                Return a legal action id from <code>run(args, obs)</code> — it runs every tick, so
                keep it fast. <code>obs["legal_actions"]</code> lists your moves.
              </div>
              <div style={{ padding: 10, flex: 1, minHeight: 0 }}>
                <CodeEditor
                  value={botTool?.code ?? ''}
                  onChange={setBotCode}
                  language="python"
                  minHeight="320px"
                  placeholder={'def run(args, obs):\n    return obs["legal_actions"][0]["id"]'}
                />
              </div>
              {!botTool && (
                <div
                  style={{
                    margin: '0 10px 10px',
                    fontSize: '0.72rem',
                    color: 'var(--text-dim)',
                  }}
                >
                  No bot yet — start typing above, or load a starter from Helper tools below.
                </div>
              )}
              <button
                type="button"
                onClick={() => setShowTools((s) => !s)}
                style={{
                  ...btn,
                  border: 'none',
                  borderTop: '1px solid var(--border, #33343a)',
                  borderRadius: 0,
                  textAlign: 'left',
                  color: 'var(--text-dim)',
                }}
              >
                {showTools ? '▾' : '▸'} Helper tools (
                {loadout.tools.filter((t) => t.name !== botTool?.name).length}) — optional Python
                your bot can call
              </button>
              {showTools && (
                <div style={{ padding: 10 }}>
                  <ToolsEditor
                    tools={loadout.tools}
                    templates={templates}
                    onChange={(tools) => patch({ tools })}
                    onApplyTemplate={applyTemplate}
                  />
                </div>
              )}
            </>
          ) : (
            // Reasoner game: the LLM harness — an optional `my_agent` over the system
            // prompt + tools + model that actually decide the move.
            <>
              <div style={{ padding: 10, flex: 1, minHeight: 0 }}>
                <CodeEditor
                  value={loadout.agent_code}
                  onChange={(v) => patch({ agent_code: v })}
                  language="python"
                  minHeight="320px"
                  placeholder={
                    'def my_agent(obs, config):\n    return obs["legal_actions"][0]["id"]'
                  }
                />
              </div>
              {agentError && (
                <div
                  style={{
                    margin: '0 10px 10px',
                    padding: '0.5rem 0.6rem',
                    borderRadius: 6,
                    fontFamily: 'var(--font-mono, monospace)',
                    fontSize: '0.72rem',
                    color: 'var(--danger, #f87171)',
                    background: 'color-mix(in srgb, var(--danger, #f87171) 12%, transparent)',
                    border: '1px solid color-mix(in srgb, var(--danger, #f87171) 40%, transparent)',
                  }}
                >
                  {agentError}
                </div>
              )}
              <button
                type="button"
                onClick={() => setShowContext((s) => !s)}
                style={{
                  ...btn,
                  border: 'none',
                  borderTop: '1px solid var(--border, #33343a)',
                  borderRadius: 0,
                  textAlign: 'left',
                  color: 'var(--text-dim)',
                }}
              >
                {showContext ? '▾' : '▸'} Context (system prompt) — the default agent’s instructions
              </button>
              {showContext && (
                <div style={{ padding: 10 }}>
                  <textarea
                    value={loadout.context}
                    onChange={(e) => patch({ context: e.target.value })}
                    placeholder="System prompt for the default agent (your context + tools drive the model)…"
                    style={{
                      width: '100%',
                      minHeight: 90,
                      resize: 'vertical',
                      boxSizing: 'border-box',
                      padding: '0.5rem',
                      fontSize: '0.8rem',
                      fontFamily: 'inherit',
                      background: 'var(--bg, #1c1c1c)',
                      color: 'var(--text)',
                      border: '1px solid var(--border, #33343a)',
                      borderRadius: 6,
                    }}
                  />
                </div>
              )}
              <button
                type="button"
                onClick={() => setShowTools((s) => !s)}
                style={{
                  ...btn,
                  border: 'none',
                  borderTop: '1px solid var(--border, #33343a)',
                  borderRadius: 0,
                  textAlign: 'left',
                  color: 'var(--text-dim)',
                }}
              >
                {showTools ? '▾' : '▸'} Tools ({loadout.tools.length}) — Python your agent can call
              </button>
              {showTools && (
                <div style={{ padding: 10 }}>
                  <ToolsEditor
                    tools={loadout.tools}
                    templates={templates}
                    onChange={(tools) => patch({ tools })}
                    onApplyTemplate={applyTemplate}
                  />
                </div>
              )}
            </>
          )}
        </div>

        {/* Loadout column */}
        <aside style={{ ...card, display: 'flex', flexDirection: 'column' }}>
          {/* game picker */}
          <div style={{ padding: 12, borderBottom: '1px solid var(--border, #33343a)' }}>
            <div style={{ ...sectionLbl, marginBottom: 8 }}>
              Match — grades{' '}
              <span style={{ color: 'var(--gold, #f5b942)' }}>{STAT_LABEL[graded]} ★</span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {games.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  onClick={() => {
                    setGameId(g.id);
                    setActiveGame(g.id);
                  }}
                  style={{
                    ...btn,
                    fontSize: '0.72rem',
                    padding: '0.3rem 0.55rem',
                    ...(g.id === gameId
                      ? {
                          borderColor: 'var(--accent, #6ea8fe)',
                          background: 'color-mix(in srgb, var(--accent, #6ea8fe) 12%, transparent)',
                          color: 'var(--text)',
                        }
                      : { color: 'var(--text-dim)' }),
                  }}
                >
                  {g.name}
                </button>
              ))}
            </div>
          </div>

          {/* ability readout */}
          <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border, #33343a)' }}>
            <div style={{ ...sectionLbl, marginBottom: 8 }}>
              Loadout <span style={{ color: 'var(--text-faint, #666)' }}>· from your code</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              <Slot on={build.context} name="Context" sub="system prompt" />
              <Slot on={build.tools > 0} name="Tools" sub={`${build.tools} custom`} />
              <Slot on={build.rag} name="RAG" sub="retrieve()" />
              <Slot on={build.memory} name="Memory" sub="config.memory" />
              <Slot on={!!build.model} name="Model" sub={build.model ?? 'default'} />
            </div>
          </div>

          {/* stats */}
          <div style={{ padding: '10px 12px', display: 'grid', gap: 8 }}>
            <div style={sectionLbl}>Agent stats</div>
            {(Object.keys(STAT_LABEL) as StatKey[]).map((k) => (
              <StatBar key={k} label={STAT_LABEL[k]} value={st[k]} graded={k === graded} />
            ))}
          </div>

          {/* power + actions */}
          <div
            style={{
              marginTop: 'auto',
              padding: 12,
              borderTop: '1px solid var(--border, #33343a)',
              display: 'flex',
              alignItems: 'center',
              gap: 12,
            }}
          >
            <div style={{ fontFamily: 'var(--font-mono, monospace)' }}>
              <div style={{ ...sectionLbl }}>Power</div>
              <div style={{ fontSize: '1.5rem', color: 'var(--gold, #f5b942)', lineHeight: 1 }}>
                {power}
              </div>
            </div>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
              <button type="button" onClick={save} style={btn}>
                Save
              </button>
              <button
                type="button"
                onClick={lockIn}
                style={{
                  ...btn,
                  border: 'none',
                  fontWeight: 700,
                  color: '#0b0b0b',
                  background: 'var(--accent, #6ea8fe)',
                }}
              >
                Lock in ▸
              </button>
            </div>
          </div>
          <div
            style={{
              padding: '0 12px 12px',
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: '0.72rem',
              color: 'var(--text-dim)',
              minHeight: 18,
            }}
          >
            {status}
          </div>
        </aside>
      </div>

      {vsOpen && (
        <VsCeremony
          agentName="my_agent"
          gameName={games.find((g) => g.id === gameId)?.name ?? gameId}
          build={build}
          power={power}
          onEnter={enterArena}
          onClose={() => setVsOpen(false)}
        />
      )}
    </div>
  );
}

function Slot({ on, name, sub }: { on: boolean; name: string; sub: string }) {
  return (
    <div
      style={{
        padding: '0.5rem 0.55rem',
        borderRadius: 8,
        border: `1px solid ${on ? 'var(--accent, #6ea8fe)' : 'var(--border, #33343a)'}`,
        background: on
          ? 'color-mix(in srgb, var(--accent, #6ea8fe) 9%, transparent)'
          : 'var(--bg, #1c1c1c)',
        opacity: on ? 1 : 0.6,
      }}
    >
      <div
        style={{
          fontFamily: 'var(--font-mono, monospace)',
          fontSize: '0.72rem',
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
          color: on ? 'var(--accent, #6ea8fe)' : 'var(--text-dim)',
        }}
      >
        {name}
      </div>
      <div style={{ fontSize: '0.66rem', color: 'var(--text-faint, #666)', marginTop: 2 }}>
        {sub}
      </div>
    </div>
  );
}

function StatBar({ label, value, graded }: { label: string; value: number; graded: boolean }) {
  const accent = graded ? 'var(--gold, #f5b942)' : 'var(--accent, #6ea8fe)';
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '80px 1fr 28px',
        alignItems: 'center',
        gap: 8,
      }}
    >
      <span
        style={{
          fontFamily: 'var(--font-mono, monospace)',
          fontSize: '0.66rem',
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: graded ? 'var(--gold, #f5b942)' : 'var(--text-dim)',
        }}
      >
        {graded ? '★ ' : ''}
        {label}
      </span>
      <span
        style={{
          height: 7,
          borderRadius: 4,
          background: 'var(--bg, #1c1c1c)',
          border: '1px solid var(--border, #33343a)',
          overflow: 'hidden',
        }}
      >
        <span
          style={{
            display: 'block',
            height: '100%',
            width: `${value}%`,
            background: accent,
            transition: 'width 0.45s cubic-bezier(0.2, 0.8, 0.2, 1)',
          }}
        />
      </span>
      <span
        style={{
          fontFamily: 'var(--font-mono, monospace)',
          fontSize: '0.72rem',
          textAlign: 'right',
          color: 'var(--text-dim)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </span>
    </div>
  );
}
