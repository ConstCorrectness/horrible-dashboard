import { useState, type CSSProperties } from 'react';

import { fetchSampleObservation, testTool, type DecisionClass } from '../games-api';
import { tutorialFor, type TutorialStep } from '../tutorials';

/**
 * **Bootcamp** — the per-game tutorial that walks a new player up to a working agent.
 * It renders the game's {@link tutorialFor track}: each step drops starter code into
 * the right editor slot (the bot tool for policy games, `agent_code` for reasoner
 * games) via the callbacks, and a `bot` step can be tested against a sampled position
 * so the player watches it pick a legal move before a match. See tutorials.ts and
 * docs/modules/games.mdx.
 */

const dim: CSSProperties = { color: 'var(--text-dim)', fontSize: '0.72rem' };
const btn: CSSProperties = {
  fontFamily: 'var(--font-mono, monospace)',
  fontSize: '0.72rem',
  padding: '0.3rem 0.6rem',
  borderRadius: 6,
  border: '1px solid var(--border, #33343a)',
  background: 'transparent',
  color: 'var(--text)',
  cursor: 'pointer',
};

export function BootcampSection({
  gameId,
  decisionClass,
  onLoadBot,
  onLoadAgent,
}: {
  gameId: string;
  decisionClass: DecisionClass;
  /** Drop a bot-target step's code into the bot tool editor. */
  onLoadBot: (code: string) => void;
  /** Drop an agent-target step's code into the `agent_code` editor. */
  onLoadAgent: (code: string) => void;
}) {
  const track = tutorialFor(gameId, decisionClass);
  const [open, setOpen] = useState(false);
  // Per-step test outcome, keyed by step id: the action the bot chose, or an error.
  const [tested, setTested] = useState<Record<string, { ok: boolean; msg: string }>>({});
  const [testing, setTesting] = useState<string | null>(null);

  const load = (step: TutorialStep) => {
    if (step.target === 'bot') onLoadBot(step.code);
    else onLoadAgent(step.code);
  };

  // Run a bot step against a freshly sampled observation and report what it chose.
  const test = async (step: TutorialStep) => {
    setTesting(step.id);
    try {
      const sample = await fetchSampleObservation(gameId, 0);
      if (!sample.ok) {
        setTested((t) => ({ ...t, [step.id]: { ok: false, msg: sample.error ?? 'no sample' } }));
        return;
      }
      const legal = new Set(sample.legal_actions.map((a) => a.id));
      const res = await testTool(step.code, sample.observation);
      if (!res.ok) {
        setTested((t) => ({ ...t, [step.id]: { ok: false, msg: res.error ?? 'error' } }));
        return;
      }
      const chosen = String(res.result);
      setTested((t) => ({
        ...t,
        [step.id]: legal.has(chosen)
          ? { ok: true, msg: `chose ${chosen} ✓` }
          : { ok: false, msg: `returned ${chosen} — not a legal action` },
      }));
    } catch (e) {
      setTested((t) => ({ ...t, [step.id]: { ok: false, msg: String(e) } }));
    } finally {
      setTesting(null);
    }
  };

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8 }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{
          ...btn,
          width: '100%',
          border: 'none',
          borderRadius: 0,
          textAlign: 'left',
          padding: '0.55rem 0.7rem',
          color: 'var(--text)',
        }}
      >
        {open ? '▾' : '▸'} 📘 Bootcamp — {track.title}
      </button>
      {open && (
        <div
          style={{ padding: '0 0.7rem 0.7rem', display: 'flex', flexDirection: 'column', gap: 10 }}
        >
          <div style={dim}>{track.intro}</div>
          {track.steps.map((step, i) => {
            const outcome = tested[step.id];
            return (
              <div
                key={step.id}
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: 6,
                  padding: '0.5rem 0.6rem',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                }}
              >
                <div style={{ fontSize: '0.8rem', fontWeight: 600 }}>
                  {i + 1}. {step.title}
                </div>
                <div style={dim}>{step.goal}</div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                  <button type="button" style={btn} onClick={() => load(step)}>
                    Load into editor
                  </button>
                  {step.target === 'bot' && (
                    <button
                      type="button"
                      style={btn}
                      disabled={testing === step.id}
                      onClick={() => void test(step)}
                    >
                      {testing === step.id ? 'testing…' : '▶ Test on sample'}
                    </button>
                  )}
                  {outcome && (
                    <span
                      style={{
                        fontSize: '0.72rem',
                        color: outcome.ok ? 'var(--success, #4ade80)' : '#e5a13f',
                      }}
                    >
                      {outcome.msg}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
