import type { CSSProperties } from 'react';

import type { LoadoutTemplate } from '../games-api';
import { CodeEditor } from './CodeEditor';

/**
 * **The coded-agent editor** — one Python policy, and nothing else.
 *
 * Its counterpart is `HarnessBuilder`. They are separate components rather than
 * branches of one, because they edit separate objects: a `CodedHarness` is
 * `bot_code`, a `LlmHarness` is context + tools + model + `agent_code`, and neither
 * carries the other's fields (see docs/modules/games.mdx). When this was one
 * component with `if (policy === 'bot')` scattered through it, every shared line had
 * to be read twice — "does this run for a bot seat?" — and the answer drifted: a
 * "Helper tools" fold survived here for a harness that has no tools, and the loading
 * gate tested the LLM readout, so every coded game rendered a permanent "loading…".
 *
 * There is no model, prompt, or tool list here to hide. That is the point.
 */
export function CodedBuilder({
  botCode,
  setBotCode,
  templates,
  error,
  onLoadStarter,
  btn,
}: {
  botCode: string;
  setBotCode: (code: string) => void;
  templates: LoadoutTemplate[];
  error: string | null;
  onLoadStarter: (t: LoadoutTemplate) => void;
  btn: CSSProperties;
}) {
  return (
    <>
      <div style={{ padding: '0.5rem 0.7rem', fontSize: '0.72rem', color: 'var(--text-dim)' }}>
        Return a legal action id from <code>act(obs, info)</code> — it runs every tick, so keep it
        fast. <code>info["legal_actions"]</code> lists your moves.
      </div>
      <div style={{ padding: 10, flex: 1, minHeight: 0 }}>
        <CodeEditor
          value={botCode}
          onChange={setBotCode}
          language="python"
          minHeight="320px"
          placeholder={'def act(obs, info):\n    return info["legal_actions"][0]["id"]'}
        />
      </div>
      {error && (
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
          {error}
        </div>
      )}
      {!botCode.trim() && (
        <div style={{ margin: '0 10px 10px', fontSize: '0.72rem', color: 'var(--text-dim)' }}>
          No bot yet — start typing above.
        </div>
      )}
      {templates.length > 0 && (
        <div style={{ padding: '0 10px 10px', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {templates.map((t) => (
            <button
              key={t.id}
              type="button"
              style={{ ...btn, fontSize: '0.72rem' }}
              title={t.blurb}
              onClick={() => onLoadStarter(t)}
            >
              ⬇ {t.title}
            </button>
          ))}
        </div>
      )}
    </>
  );
}
