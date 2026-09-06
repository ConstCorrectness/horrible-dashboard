/**
 * The model dropdown in the Agent pane's session bar: which model answers *this*
 * agent's turns, switchable without leaving the conversation.
 *
 * **It sets the provider and the model together**, and that is the whole design.
 * The two are separate settings server-side (`agent.<id>.provider` and
 * `agent.<id>.model`, falling back to `agent.orchestrator.*` — see
 * backend/modules/agent/roster.py), and the settings page edits them as two
 * controls because that is what it is for. Here they are one choice, because a
 * model name means nothing on a server that does not have it: picking
 * `minimax/minimax-m3:free` while the provider still says Ollama is not a partial
 * setup, it is a broken one, and it fails mid-turn rather than at the click. An
 * option therefore carries both halves and writing one without the other is not
 * expressible.
 *
 * Options come from `/agent/status`, grouped by provider — so a hosted provider
 * appears here the moment a key is saved (see ApiKeysSettings), and an unreachable
 * provider contributes nothing because it reports no models.
 *
 * **It is a combobox, not a `<select>`.** One OpenRouter key puts several hundred
 * models in this list; a native select can only be walked, and its type-ahead
 * matches a *prefix*, so the thing people actually want — every `:free` model, or
 * everything with `qwen` in it — is unreachable by typing. The filter is a
 * substring match over the provider label and the model id together, which is
 * what makes `free` and `gemma` and `lmstudio` all work as queries.
 *
 * The panel is `position: fixed` and measured off the trigger: the session bar is
 * a narrow flex row inside a pane that clips its overflow, so an absolutely
 * positioned list would be cut off after two rows.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { resetSetting, setSetting, useSetting } from '../../settings';
import { getAgentStatus, type AgentStatus } from './api';

/** `provider::model`. A single option value, because the two are chosen together. */
function encode(kind: string, model: string): string {
  return `${kind}::${model}`;
}

interface Choice {
  /** `provider::model`, or '' for "use the configured model". */
  value: string;
  /** What the row shows as its identity — the model id, in mono. */
  label: string;
  /** The provider it belongs to, shown beside the id and searched with it. */
  group: string;
}

export function ModelPicker({
  agentId,
  status,
  disabled,
}: {
  agentId: string;
  /** The pane's own status, reused so the bar does not re-probe every provider.
   * Null while it is still loading or the backend is down. */
  status: AgentStatus | null;
  disabled?: boolean;
}) {
  // `main` predates the roster and keeps the original settings namespace.
  const prefix = agentId === 'main' ? 'agent.orchestrator' : `agent.${agentId}`;
  const PROVIDER_KEY = `${prefix}.provider`;
  const MODEL_KEY = `${prefix}.model`;

  const provider = useSetting<string>(PROVIDER_KEY) ?? '';
  const model = useSetting<string>(MODEL_KEY) ?? '';

  // Falls back to its own fetch only when the pane could not hand one over, so the
  // picker still works in a pane that mounted while the backend was restarting.
  const [own, setOwn] = useState<AgentStatus | null>(null);
  useEffect(() => {
    if (status) return;
    void getAgentStatus()
      .then(setOwn)
      .catch(() => {
        /* backend down — the picker renders nothing rather than an empty list */
      });
  }, [status]);

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [anchor, setAnchor] = useState<{
    left: number;
    top: number;
    width: number;
    maxHeight: number;
  } | null>(null);

  const live = status ?? own;
  const providers = useMemo(() => live?.providers ?? [], [live]);

  const effective = provider && model ? encode(provider, model) : '';
  const known = providers.some((p) => p.kind === provider && p.models.includes(model));
  const configuredLabel = live?.model
    ? `Configured (${live.model})`
    : agentId === 'main'
      ? 'Configured model'
      : 'Orchestrator model';

  const choices = useMemo<Choice[]>(() => {
    const out: Choice[] = [{ value: '', label: configuredLabel, group: 'Default' }];
    // The saved override may name a model the live list does not have — a provider
    // that went down, or a model id typed on the settings page. Keeping it as a
    // choice is what stops the trigger from silently rendering as something else.
    if (effective && !known) out.push({ value: effective, label: model, group: provider });
    for (const p of providers) {
      for (const m of new Set(p.models)) {
        out.push({ value: encode(p.kind, m), label: m, group: p.label });
      }
    }
    return out;
  }, [providers, configuredLabel, effective, known, model, provider]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return choices;
    // Every whitespace-separated word must appear somewhere in "group label", so
    // `free qwen` narrows rather than widening the way an OR would.
    const words = q.split(/\s+/);
    return choices.filter((c) => {
      const hay = `${c.group} ${c.label}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
  }, [choices, query]);

  // Keep the highlight on a row that exists as the query narrows the list.
  useEffect(() => setCursor(0), [query]);

  // Measure the trigger, and keep measuring: the pane can be dragged or resized
  // with the panel open, and a fixed panel does not follow on its own.
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const r = triggerRef.current?.getBoundingClientRect();
      if (!r) return;
      // Wide enough for an OpenRouter id (`inclusionai/ling-3.0-flash-sante:free`);
      // the trigger is ~11rem, and at that width every row ellipsises to the
      // vendor prefix, which is the half that does not identify the model.
      const width = Math.min(Math.max(r.width, 340), window.innerWidth - 16);
      // Clamp to the viewport rather than letting it run off: the bar sits at the
      // right edge of a window that is often itself at the right edge of the screen.
      const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
      // Flip above when the space below cannot hold a usable list.
      const below = window.innerHeight - r.bottom - 12;
      const above = r.top - 12;
      const flip = below < 200 && above > below;
      const maxHeight = Math.max(140, Math.min(352, flip ? above : below));
      setAnchor({ left, top: flip ? r.top - 4 - maxHeight : r.bottom + 4, width, maxHeight });
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [open]);

  useEffect(() => {
    if (open) searchRef.current?.focus();
    else setQuery('');
  }, [open]);

  // Close on a click anywhere else. Pointerdown rather than click so it closes
  // before the thing under the pointer reacts.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || triggerRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [open]);

  // Scroll the highlighted row into view when the keyboard moves it.
  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector('[data-cursor="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [cursor, open]);

  if (!live) return null;

  const choose = (value: string): void => {
    setOpen(false);
    if (value === '') {
      void resetSetting(PROVIDER_KEY);
      void resetSetting(MODEL_KEY);
      return;
    }
    const sep = value.indexOf('::');
    void setSetting(PROVIDER_KEY, value.slice(0, sep));
    void setSetting(MODEL_KEY, value.slice(sep + 2));
  };

  const current = choices.find((c) => c.value === effective) ?? choices[0];

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setCursor((i) => (filtered.length ? (i + 1) % filtered.length : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setCursor((i) => (filtered.length ? (i - 1 + filtered.length) % filtered.length : 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (filtered[cursor]) choose(filtered[cursor].value);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="agent-model-picker"
        aria-label="Model"
        aria-haspopup="listbox"
        aria-expanded={open}
        title={
          effective
            ? `${provider} · ${model}`
            : 'Model for this agent — blank uses the one you configured during onboarding'
        }
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="agent-model-picker-value">{current.label}</span>
        <svg className="agent-model-picker-caret" viewBox="0 0 10 10" aria-hidden="true">
          <path d="M2 4l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      </button>
      {open && anchor && (
        <div
          ref={panelRef}
          className="agent-model-menu"
          style={{
            left: anchor.left,
            top: anchor.top,
            width: anchor.width,
            maxHeight: anchor.maxHeight,
          }}
          role="dialog"
        >
          <input
            ref={searchRef}
            className="agent-model-search"
            value={query}
            placeholder="Filter models…"
            aria-label="Filter models"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <ul className="agent-model-list" role="listbox" aria-label="Model">
            {filtered.length === 0 && <li className="agent-model-empty">No model matches</li>}
            {filtered.map((c, i) => (
              <li key={c.value || '__default'}>
                <button
                  type="button"
                  role="option"
                  aria-selected={c.value === effective}
                  data-cursor={i === cursor}
                  className={`agent-model-option${i === cursor ? ' is-cursor' : ''}${
                    c.value === effective ? ' is-selected' : ''
                  }`}
                  onPointerEnter={() => setCursor(i)}
                  onClick={() => choose(c.value)}
                >
                  <span className="agent-model-option-name">{c.label}</span>
                  <span className="agent-model-option-group">{c.group}</span>
                </button>
              </li>
            ))}
          </ul>
          <p className="agent-model-count">
            {filtered.length} of {choices.length}
          </p>
        </div>
      )}
    </>
  );
}
