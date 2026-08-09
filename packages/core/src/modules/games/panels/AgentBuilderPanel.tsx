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
import { decisionClassOf, GAME_CATEGORIES } from '../game-identity';
import {
  fetchGamesCatalog,
  fetchLoadoutTemplates,
  getAgentStarter,
  getLoadout,
  harnessKindForPolicy,
  saveLoadout,
  validateLoadout,
  type GameCatalogEntry,
  type Harness,
  type Loadout,
  type LoadoutTemplate,
  type MovePolicy,
} from '../games-api';
import { setGamesSection } from '../hub-section';
import { startWithSavedSetup } from '../matchmaking';
import { setActiveGame, useActiveGame } from '../selected-game';
import { BootcampSection } from './BootcampSection';
import { CodedBuilder } from './CodedBuilder';
import { HarnessBuilder } from './HarnessBuilder';
import { HarnessPipeline } from './HarnessPipeline';
import { ObservationInspector } from './ObservationInspector';
import { VsCeremony } from './VsCeremony';

/**
 * **Build your agent** — the shell around the two builders.
 *
 * There is no single editor, because there is no single harness. This component owns
 * what both need — the game picker, the category header, the pipeline strip, the
 * observation inspector, save/lock-in — and hands the editing itself to
 * `CodedBuilder` (a Python policy: `bot_code`, no model) or `HarnessBuilder`
 * (`my_agent` + system prompt + tools + model). Which one you get follows the
 * **seat**, not the game: `harnessKindForPolicy` reads the effective move policy, so
 * a turn-based coded game on the escape hatch shows the LLM builder when its driver
 * is set to the LLM agent.
 *
 * The right-hand readout is a reading of the LLM harness (which abilities the code
 * actually uses, and the stats that follow), so it is replaced by a short note for a
 * coded harness rather than drawn as five empty slots — an empty readout would look
 * like a weak agent instead of the wrong question.
 *
 * See docs/modules/games.mdx and backend agent_sdk.py.
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
  const [harness, setHarness] = useState<Harness | null>(null);
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

  // Follow the shared selection: when the library (or another panel) switches the
  // active game, switch the harness to match.
  useEffect(() => {
    if (activeGame) setGameId(activeGame);
  }, [activeGame]);

  // The game's category + presentation, from the catalog (fallback: the id map).
  const entry = games.find((g) => g.id === gameId);
  const decisionClass = entry?.decision_class ?? decisionClassOf(gameId);
  // The effective move policy for THIS game (per-game override → catalog default),
  // and the harness kind that follows from it. Which harness you're editing is a
  // property of the SEAT, not the game: a turn-based coded game on the escape hatch
  // has both, and your driver choice is what says which one plays.
  const policyOverride = useSetting<string>(`games.policy.${gameId}`);
  const policy: MovePolicy = (policyOverride ??
    entry?.default_policy ??
    (decisionClass === 'reasoner' ? 'agent' : 'bot')) as MovePolicy;
  const kind = harnessKindForPolicy(policy);

  // The shipped templates for this game — the editor's starting points, narrowed
  // to the harness being edited so a coded builder is never offered a tool list it
  // has nowhere to put.
  useEffect(() => {
    let cancelled = false;
    fetchLoadoutTemplates(gameId, kind)
      .then((t) => !cancelled && setTemplates(t))
      .catch(() => !cancelled && setTemplates([]));
    return () => {
      cancelled = true;
    };
  }, [gameId, kind]);

  // Load the harness this game's seat actually runs. `kind` is derived from the
  // effective move policy, so a coded seat gets bot_code and an LLM seat gets the
  // prompt+tools — never a blend of the two, and never the other one's editor.
  useEffect(() => {
    let cancelled = false;
    setStatus('loading…');
    setAgentError(null);
    getLoadout(gameId, kind)
      .then(async (h) => {
        if (h.kind === 'llm' && !h.agent_code?.trim()) {
          try {
            h = { ...h, agent_code: (await getAgentStarter(gameId)).agent_code };
          } catch {
            // starter unavailable — leave blank (still valid: the default agent)
          }
        }
        if (!cancelled) {
          setHarness(h);
          setStatus('');
        }
      })
      .catch((e: Error) => !cancelled && setStatus(String(e.message || e)));
    return () => {
      cancelled = true;
    };
  }, [gameId, kind]);

  // `readBuild` and the stats it feeds describe the LLM harness (context, tools,
  // model); a coded harness has none of those, so the whole strip is llm-only.
  const loadout = harness?.kind === 'llm' ? harness : null;
  const build = useMemo(() => (loadout ? readBuild(loadout) : null), [loadout]);
  const st = useMemo(() => (build ? stats(build) : null), [build]);
  const graded = gradedStat(gameId);
  const power = useMemo(() => (build ? buildPower(build) : 0), [build]);

  const obsKind = entry?.obs_kind;
  const category = GAME_CATEGORIES.find((c) => c.cls === decisionClass) ?? GAME_CATEGORIES[0];

  const patch = useCallback(
    (p: Partial<Loadout>) => setHarness((h) => (h && h.kind === 'llm' ? { ...h, ...p } : h)),
    [],
  );

  // The coded harness *is* the bot: one body, edited directly. It used to be a tool
  // inside the LLM harness's tool list, found by name (`<game>.bot` → `bot`) — which
  // is why this needed a resolution rule, an insert-if-missing branch, and a matching
  // rule on the backend that could drift out of sync with it.
  const botCode = harness?.kind === 'coded' ? harness.bot_code : '';
  const setBotCode = useCallback(
    (code: string) => setHarness((h) => (h && h.kind === 'coded' ? { ...h, bot_code: code } : h)),
    [],
  );

  // Load a shipped coded starter. Unlike the LLM side there is nothing to merge
  // into — a policy is one body — so this REPLACES, and asks first when that would
  // discard something you wrote.
  const loadStarter = useCallback(
    (t: LoadoutTemplate) => {
      const starter = (t.loadout as unknown as { bot_code?: string }).bot_code ?? '';
      if (botCode.trim() && !window.confirm(`Replace your bot with "${t.title}"?`)) return;
      setBotCode(starter);
      setStatus(`loaded starter "${t.title}"`);
    },
    [botCode, setBotCode],
  );

  // Load a shipped template's tool definitions as a starting point. Additive on
  // purpose: a template is a gift, not a reset — it appends the tools you don't
  // already have (matching by name, so re-applying is idempotent and never clobbers
  // a tool you've since edited) and only adopts its context when you have none.
  const applyTemplate = useCallback((t: LoadoutTemplate) => {
    setHarness((h) => {
      if (!h || h.kind !== 'llm') return h;
      const have = new Set(h.tools.map((x) => x.name));
      const added = (t.loadout.tools ?? []).filter((x) => !have.has(x.name));
      return {
        ...h,
        tools: [...h.tools, ...added],
        context: h.context.trim() ? h.context : t.loadout.context,
      };
    });
    setShowTools(true);
    setStatus(`loaded template "${t.title}"`);
  }, []);

  // Validate + persist the active version. Returns true if the harness is saveable
  // (compiles); false surfaces the error and blocks lock-in.
  const save = useCallback(async (): Promise<boolean> => {
    if (!harness) return false;
    setStatus('saving…');
    setAgentError(null);
    try {
      const v = await validateLoadout(harness);
      if (v.agent_error) {
        setAgentError(v.agent_error);
        setStatus(harness.kind === 'coded' ? 'bot has an error' : 'agent has an error');
        return false;
      }
      await saveLoadout(gameId, harness);
      setStatus('saved ✓');
      return true;
    } catch (e) {
      setStatus(String((e as Error).message || e));
      return false;
    }
  }, [harness, gameId]);

  // Lock in: save, then open the VS ceremony (only if the agent actually compiles).
  const lockIn = useCallback(async () => {
    if (await save()) setVsOpen(true);
  }, [save]);

  // From the ceremony's "Enter arena": start a real self-play match (no sign-in
  // needed — it plays on this node), which switches this pane to the Game Board
  // section; also open the Games Log so the reasoning streams as the agent plays.
  const enterArena = useCallback(() => {
    setVsOpen(false);
    void startWithSavedSetup(gameId, entry?.default_policy, entry?.allowed_policies);
    openDrawer('log');
  }, [gameId]);

  // The loading gate is about the HARNESS, not the LLM readout: `build`/`st`
  // describe context+tools+model and are null for a coded harness by design, so
  // testing them here rendered a permanent "loading…" over every coded game.
  if (!harness || (harness.kind === 'llm' && (!build || !st))) {
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
      {/* Category header + the harness pipeline + a live observation, so the build
          reads against reality: what your agent sees, what runs, what it returns.
          The badge names the GAME's category and the sentence names the harness YOU
          are editing — they differ only on a hatch game, and that difference is
          exactly the thing worth saying out loud. */}
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
            {category.icon} {category.label}
          </span>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
            {harness.kind === 'coded'
              ? 'You are writing the policy: obs → action, no model in the loop.'
              : decisionClass === 'policy'
                ? 'This seat is running the LLM harness — your prompt, tools and model pick each move.'
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
              {harness.kind === 'coded' ? `${gameId}_bot.py` : 'my_agent.py'}
            </span>
            <span style={{ ...sectionLbl, color: 'var(--text-faint, #666)' }}>
              · {harness.kind === 'coded' ? 'coded agent · obs → action' : `llm agent · ${gameId}`}
            </span>
          </div>

          {harness.kind === 'coded' ? (
            <CodedBuilder
              botCode={botCode}
              setBotCode={setBotCode}
              templates={templates}
              error={agentError}
              onLoadStarter={loadStarter}
              btn={btn}
            />
          ) : (
            <HarnessBuilder
              harness={harness}
              patch={patch}
              templates={templates}
              onApplyTemplate={applyTemplate}
              agentError={agentError}
              showContext={showContext}
              setShowContext={setShowContext}
              showTools={showTools}
              setShowTools={setShowTools}
              btn={btn}
            />
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

          {/* Ability readout + stats — a reading of the LLM harness (context, tools,
              RAG, memory, model), so a coded harness has nothing to read here. The
              honest answer is to say so rather than draw five empty slots and a row
              of zeroed bars, which would look like a weak agent instead of the wrong
              question. */}
          {build && st ? (
            <>
              <div
                style={{ padding: '10px 12px', borderBottom: '1px solid var(--border, #33343a)' }}
              >
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

              <div style={{ padding: '10px 12px', display: 'grid', gap: 8 }}>
                <div style={sectionLbl}>Agent stats</div>
                {(Object.keys(STAT_LABEL) as StatKey[]).map((k) => (
                  <StatBar key={k} label={STAT_LABEL[k]} value={st[k]} graded={k === graded} />
                ))}
              </div>
            </>
          ) : (
            <div style={{ padding: '10px 12px', display: 'grid', gap: 8 }}>
              <div style={sectionLbl}>Coded agent</div>
              <p style={{ fontSize: '0.75rem', color: 'var(--text-dim)', margin: 0 }}>
                No model, no prompt, no tools — this seat is your policy and nothing else. Measure
                it in <strong>Train</strong>: a few hundred headless episodes give you a win rate in
                about a second.
              </p>
            </div>
          )}

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
          // A coded harness has no ability readout, so the ceremony shows an empty
          // build rather than a fabricated one.
          agentName={harness.kind === 'coded' ? 'my bot' : 'my_agent'}
          gameName={games.find((g) => g.id === gameId)?.name ?? gameId}
          build={build ?? { context: false, tools: 0, rag: false, memory: false, model: null }}
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
