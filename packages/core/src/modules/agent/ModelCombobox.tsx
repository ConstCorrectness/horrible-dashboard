/**
 * A free-text model field with a searchable suggestion list — the onboarding and
 * settings-page counterpart of `ModelPicker`.
 *
 * It replaces a `<datalist>` (onboarding) and a `<select>` (the settings page's
 * model override), which failed the same query: with several hundred OpenRouter
 * ids, both only effectively match from the start of the string, so typing `free`
 * found nothing. Filtering goes through `matchesModelQuery`, the same substring
 * match the Agent pane's picker uses.
 *
 * **The value stays free text.** A provider's list is a starting point, never the
 * whole range, so Enter on a query that matches no row uses what was typed.
 * Edits are committed on pick, Enter or blur rather than per keystroke: on the
 * settings page every commit is a settings write, and a half-typed id saved as the
 * model override is a broken agent until the typing stops.
 *
 * **Free models are listed first and tagged**, and a free model also matches the
 * word `free` — so the query finds zero-priced ids whose name does not happen to
 * end in `:free`.
 *
 * The panel is `position: fixed` and measured off the input for the same reason as
 * the picker's: its hosts (a settings row, the onboarding card) clip overflow.
 */
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { matchesModelQuery } from './model-filter';

interface Row {
  value: string;
  label: string;
  free: boolean;
}

interface Anchor {
  left: number;
  top: number;
  width: number;
  maxHeight: number;
}

export function ModelCombobox({
  value,
  onChange,
  models,
  freeModels = [],
  placeholder,
  emptyLabel,
  ariaLabel = 'Model',
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  /** Suggestions. The field accepts ids that are not in it. */
  models: readonly string[];
  /** Ids that cost nothing to call; listed first, tagged, and suggested even when
   * absent from `models`. */
  freeModels?: readonly string[];
  placeholder?: string;
  /** When set, a first row that commits `''` — "use the configured model". */
  emptyLabel?: string;
  ariaLabel?: string;
  disabled?: boolean;
}) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [filter, setFilter] = useState('');
  const [cursor, setCursor] = useState(-1);
  const [anchor, setAnchor] = useState<Anchor | null>(null);

  // Follow the value from outside whenever the field is not being edited.
  useEffect(() => {
    if (!open) setDraft(value);
  }, [value, open]);

  const rows = useMemo<Row[]>(() => {
    const free = new Set(freeModels);
    const all = [...new Set([...models, ...freeModels])];
    const ordered = [...all.filter((m) => free.has(m)), ...all.filter((m) => !free.has(m))];
    const out: Row[] =
      emptyLabel !== undefined ? [{ value: '', label: emptyLabel, free: false }] : [];
    for (const m of ordered) out.push({ value: m, label: m, free: free.has(m) });
    return out;
  }, [models, freeModels, emptyLabel]);

  const filtered = useMemo(
    () =>
      rows.filter((r) =>
        r.value === ''
          ? filter.trim() === ''
          : matchesModelQuery(r.free ? `${r.label} free` : r.label, filter),
      ),
    [rows, filter],
  );
  const modelCount = rows.filter((r) => r.value !== '').length;
  const shownCount = filtered.filter((r) => r.value !== '').length;

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const r = inputRef.current?.getBoundingClientRect();
      if (!r) return;
      // Wide enough for an OpenRouter id; a narrow settings column would otherwise
      // ellipsise every row down to its vendor prefix.
      const width = Math.min(Math.max(r.width, 340), window.innerWidth - 16);
      const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
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

  // Close on a pointer press anywhere else; the input's blur then commits.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || inputRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [open]);

  useEffect(() => {
    if (!open || cursor < 0) return;
    panelRef.current?.querySelector('[data-cursor="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [cursor, open]);

  const commit = (next: string): void => {
    setOpen(false);
    setFilter('');
    setCursor(-1);
    setDraft(next);
    if (next !== value) onChange(next);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setCursor((i) => (filtered.length ? (i + 1) % filtered.length : -1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (!open) return;
      setCursor((i) => (filtered.length ? (i <= 0 ? filtered.length - 1 : i - 1) : -1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const row = cursor >= 0 ? filtered[cursor] : undefined;
      commit(row ? row.value : draft.trim());
    } else if (e.key === 'Escape' && open) {
      // Revert the edit and close — but only consume Escape while the list is
      // open, so a closed field still lets it reach the shell's Escape ladder.
      e.preventDefault();
      e.stopPropagation();
      setDraft(value);
      setFilter('');
      setCursor(-1);
      setOpen(false);
    }
  };

  const typed = draft.trim();

  return (
    <>
      <input
        ref={inputRef}
        type="text"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        value={draft}
        placeholder={emptyLabel ?? placeholder}
        spellCheck={false}
        autoComplete="off"
        disabled={disabled}
        onFocus={() => {
          // Focusing shows the whole list, not just rows matching the current value.
          setFilter('');
          setOpen(true);
        }}
        onClick={() => setOpen(true)}
        onChange={(e) => {
          setDraft(e.target.value);
          setFilter(e.target.value);
          setCursor(-1);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
        onBlur={() => commit(typed)}
      />
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
        >
          <ul id={listId} className="agent-model-list" role="listbox" aria-label={ariaLabel}>
            {filtered.length === 0 && (
              <li className="agent-model-empty">
                {typed
                  ? `No listed model matches — Enter uses “${typed}”`
                  : 'No models listed — type a model id'}
              </li>
            )}
            {filtered.map((r, i) => (
              <li key={r.value || '__default'}>
                <button
                  type="button"
                  role="option"
                  aria-selected={r.value === value}
                  data-cursor={i === cursor}
                  className={`agent-model-option${i === cursor ? ' is-cursor' : ''}${
                    r.value === value ? ' is-selected' : ''
                  }`}
                  // Keep focus in the input, so picking a row is not also a blur
                  // that commits the half-typed query first.
                  onPointerDown={(e) => e.preventDefault()}
                  onPointerEnter={() => setCursor(i)}
                  onClick={() => commit(r.value)}
                >
                  <span className="agent-model-option-name">{r.label}</span>
                  {r.free && <span className="agent-model-option-group">free</span>}
                </button>
              </li>
            ))}
          </ul>
          <p className="agent-model-count">
            {shownCount} of {modelCount}
          </p>
        </div>
      )}
    </>
  );
}
