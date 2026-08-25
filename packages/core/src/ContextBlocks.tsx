/**
 * Renderers for an assembled prompt: the composition bar, one context block, the
 * tool list, and the badge that says how much the token counts can be trusted.
 *
 * These started inside the interpretability pane. Agentpedia's stepper shows the
 * same objects — a round's blocks are a round's blocks — and a module must not
 * reach into another module's internals, so they live in core and both import them
 * from here. Same call `Avatar3D` got.
 *
 * The types are declared **structurally** rather than imported from either module:
 * core is below the modules, and a shared component that imports a module's store
 * inverts the dependency it exists to avoid. The interpretability store's own
 * `ContextBlock`/`RoundSnapshot` satisfy these, and agentpedia's API types are
 * declared to match.
 *
 * Styling is the `interp-*` class family in `packages/ui/src/styles.css`, which is
 * global — the names are kept as they were rather than renamed to something neutral,
 * because a rename would be a large diff whose only effect is on a stylesheet
 * nobody would think to grep.
 */
import { useMemo, useState } from 'react';

export interface ContextBlockView {
  kind: string;
  role: string;
  label: string;
  content: string;
  tokens: number;
  clipped: boolean;
  fullChars: number;
}

export interface ToolEntryView {
  name: string;
  group: string;
  tokens: number;
}

export interface RoundView {
  round: number;
  blocks: ContextBlockView[];
  tools: ToolEntryView[];
  messageTokens: number;
  toolTokens: number;
  totalTokens: number;
  toolsSelected: number;
  toolBudget: number;
  toolsTruncated: boolean;
  activeGroups: string[];
}

export function fmtTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

/** Stacked bar showing what the context is made of, by real token share. */
export function CompositionBar({ round }: { round: RoundView }) {
  const segments = useMemo(() => {
    const byKind = new Map<string, number>();
    for (const b of round.blocks) byKind.set(b.kind, (byKind.get(b.kind) ?? 0) + b.tokens);
    if (round.toolTokens > 0) byKind.set('tools', round.toolTokens);
    return [...byKind.entries()].filter(([, tokens]) => tokens > 0).sort((a, b) => b[1] - a[1]);
  }, [round]);

  const total = round.totalTokens || 1;
  return (
    <div className="interp-composition">
      <div className="interp-bar" role="img" aria-label="Context composition by token share">
        {segments.map(([kind, tokens]) => (
          <div
            key={kind}
            className={`interp-seg interp-kind-${kind}`}
            style={{ width: `${(tokens / total) * 100}%` }}
            title={`${kind}: ${tokens} tokens (${((tokens / total) * 100).toFixed(1)}%)`}
          />
        ))}
      </div>
      <div className="interp-legend">
        {segments.map(([kind, tokens]) => (
          <span key={kind} className="interp-legend-item">
            <i className={`interp-swatch interp-kind-${kind}`} />
            {kind} <b>{fmtTokens(tokens)}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

export function BlockRow({ block }: { block: ContextBlockView }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="interp-block">
      <button className="interp-block-head" onClick={() => setOpen((v) => !v)}>
        <span className={`interp-chip interp-kind-${block.kind}`}>{block.label}</span>
        <span className="interp-dim">{block.role}</span>
        <span className="interp-tokens">{fmtTokens(block.tokens)} tok</span>
        <span className="interp-caret">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <pre className="interp-content">
          {block.content}
          {block.clipped && (
            <span className="interp-dim">
              {`\n\n… preview clipped (${block.fullChars.toLocaleString()} chars total; ` +
                `the token count above is for the full text)`}
            </span>
          )}
        </pre>
      )}
    </div>
  );
}

/** Tool schemas for the round, grouped — usually the largest single block of context. */
export function ToolList({ round }: { round: RoundView }) {
  const groups = useMemo(() => {
    const map = new Map<string, ToolEntryView[]>();
    for (const t of round.tools) {
      const list = map.get(t.group) ?? [];
      list.push(t);
      map.set(t.group, list);
    }
    return [...map.entries()]
      .map(([group, tools]) => ({
        group,
        tools: tools.sort((a, b) => b.tokens - a.tokens),
        tokens: tools.reduce((sum, t) => sum + t.tokens, 0),
      }))
      .sort((a, b) => b.tokens - a.tokens);
  }, [round]);

  const dropped = round.toolsSelected - round.toolBudget;
  return (
    <div className="interp-tools">
      <div className="interp-subhead">
        Tools this round: <b>{round.tools.length}</b> · {fmtTokens(round.toolTokens)} tok
        {round.activeGroups.length > 0 && (
          <span className="interp-dim"> · groups: {round.activeGroups.join(', ')}</span>
        )}
      </div>
      {round.toolsTruncated && (
        <div className="interp-warn">
          ⚠ Tool budget exceeded — <b>{dropped}</b> tool{dropped === 1 ? '' : 's'} dropped before
          this prompt was sent ({round.toolsSelected} selected, budget {round.toolBudget}). The
          model cannot call what it was never shown.
        </div>
      )}
      {groups.map(({ group, tools, tokens }) => (
        <div key={group} className="interp-tool-group">
          <div className="interp-tool-group-head">
            <span className="interp-chip interp-kind-tools">{group || 'core'}</span>
            <span className="interp-dim">{tools.length} tools</span>
            <span className="interp-tokens">{fmtTokens(tokens)} tok</span>
          </div>
          {tools.map((t) => (
            <div key={t.name} className="interp-tool-row">
              <code>{t.name}</code>
              <span className="interp-tokens">{t.tokens}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * How much the token counts can be trusted. Three states, not two: a same-family
 * tokenizer of the wrong generation (Gemma 2's vocab for a Gemma 3/4 model) yields
 * numbers that *look* precise and are quietly wrong, so it gets its own label
 * rather than being folded in with a genuine match.
 *
 * Takes the three fields rather than a turn object, because the two callers spell
 * a turn differently (`tokenizerRepo` vs `tokenizer_repo`) and the badge has no
 * reason to care which.
 */
export function TokenizerBadge({
  exact,
  repo,
  source,
}: {
  exact: boolean;
  repo: string | null;
  source: string;
}) {
  if (source === 'family') {
    return (
      <span
        className="interp-approx-chip"
        title={
          `Counted with ${repo} — the right model family, but not ` +
          'necessarily the right generation, and vocabularies change between them. ' +
          'Treat these as close, not authoritative. Set interpretability.tokenizerRepo ' +
          "to this model's own tokenizer repo to make them exact."
        }
      >
        approx
      </span>
    );
  }
  if (!exact) {
    return (
      <span
        className="interp-warn-chip"
        title={
          'No tokenizer available for this model, so counts are chars/4 estimates. ' +
          'Set interpretability.tokenizerRepo, or connect Hugging Face if the ' +
          "model's tokenizer repo is gated."
        }
      >
        estimated
      </span>
    );
  }
  return repo ? (
    <span className="interp-dim" title={`Counted with ${repo}`}>
      exact
    </span>
  ) : null;
}
