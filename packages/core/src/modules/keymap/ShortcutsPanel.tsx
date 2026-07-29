/**
 * Keyboard Shortcuts — the customization surface.
 *
 * One row per command, showing every binding that resolves to it, why a binding
 * is or isn't live, and whether the host will even deliver the chord. That last
 * column is the point: `mod+1` has never worked in the browser build (Chrome's
 * tab switching is not cancellable) and nothing ever said so.
 */
import { useMemo, useState } from 'react';

import { useAgentContext } from '../../agent-context';
import { registry } from '../../registry';
import { bindingsFor, explainBinding, type ResolvedBinding } from '../../keymap/resolve';
import { checkReserved } from '../../keymap/reserved';
import { labelSpec, specsFromEvent, tryParseSpec } from '../../keymap/spec';
import { useKeyContext, useKeymap } from '../../keymap/state';
import {
  disableKeybinding,
  isCommandCustomized,
  resetKeybindings,
  setKeybinding,
} from '../../keymap/overrides';
import { toastsStore } from '../../toasts';

interface Row {
  command: string;
  title: string;
  bindings: ResolvedBinding[];
}

/** Which command a captured chord is being assigned to, and what it replaces. */
interface Recording {
  command: string;
  replaces?: { key: string; command: string };
}

function BindingChip({
  binding,
  all,
}: {
  binding: ResolvedBinding;
  all: readonly ResolvedBinding[];
}) {
  const ctx = useKeyContext();
  const chord = tryParseSpec(binding.key);
  if (!chord) return null;
  const reserved = checkReserved(chord, ctx);
  const why = explainBinding(binding, all, ctx);

  const notes: string[] = [];
  if (reserved && !reserved.preventable) notes.push(`${reserved.owner} takes this key`);
  else if (reserved) notes.push(`overrides ${reserved.owner}`);
  if (why.reason === 'shadowed') notes.push(`shadowed by ${why.by.command}`);
  if (why.reason === 'captured')
    notes.push(`suppressed while ${why.by ?? 'a pane'} has the keyboard`);
  if (binding.when) notes.push(binding.when);

  const dead = !!reserved && !reserved.preventable;
  return (
    <span className={`keymap-chip${dead ? ' keymap-chip--dead' : ''}`} title={notes.join(' · ')}>
      <kbd>{labelSpec(chord, { platform: ctx.platform })}</kbd>
      {binding.source === 'user' && <em className="keymap-chip-tag">custom</em>}
      {dead && <em className="keymap-chip-warn">✕</em>}
    </span>
  );
}

export function ShortcutsPanel() {
  const bindings = useKeymap();
  const ctx = useKeyContext();
  const [filter, setFilter] = useState('');
  const [onlyCustom, setOnlyCustom] = useState(false);
  const [recording, setRecording] = useState<Recording | null>(null);

  const rows: Row[] = useMemo(() => {
    const commands = registry.commands;
    return commands
      .map((c) => ({ command: c.id, title: c.title, bindings: bindingsFor(c.id, bindings, ctx) }))
      .filter((r) => (onlyCustom ? isCommandCustomized(r.command) : true));
  }, [bindings, ctx, onlyCustom]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.title.toLowerCase().includes(q) ||
        r.command.toLowerCase().includes(q) ||
        r.bindings.some((b) => b.key.toLowerCase().includes(q)),
    );
  }, [rows, filter]);

  useAgentContext(() => ({
    filter,
    onlyCustom,
    commands: visible.length,
    customized: rows.filter((r) => isCommandCustomized(r.command)).length,
  }));

  const capture = async (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!recording) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === 'Escape') {
      setRecording(null);
      return;
    }
    const specs = specsFromEvent(e.nativeEvent);
    if (!specs) return; // a bare modifier — keep waiting for the real key
    try {
      await setKeybinding({
        key: specs.key,
        command: recording.command,
        replaces: recording.replaces,
      });
      toastsStore.add('success', 'Shortcut updated', `${specs.key} → ${recording.command}`, 2500);
    } catch (err) {
      toastsStore.add('error', 'Could not bind', String(err), 4000);
    }
    setRecording(null);
  };

  return (
    <div className="keymap-panel">
      <div className="keymap-toolbar">
        <input
          className="keymap-search"
          placeholder="Search commands or keys…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <label className="keymap-toggle">
          <input
            type="checkbox"
            checked={onlyCustom}
            onChange={(e) => setOnlyCustom(e.target.checked)}
          />
          Customized only
        </label>
      </div>

      {recording && (
        <div className="keymap-recording">
          Press the new shortcut for <code>{recording.command}</code> — Esc to cancel.
          {/* An always-focused input is what makes the chord land here instead of
              firing whatever it is currently bound to. */}
          <input
            autoFocus
            className="keymap-recorder"
            onKeyDown={capture}
            onBlur={() => setRecording(null)}
            readOnly
            value=""
          />
        </div>
      )}

      <div className="keymap-rows">
        {visible.map((row) => (
          <div className="keymap-row" key={row.command}>
            <div className="keymap-row-main">
              <span className="keymap-row-title">{row.title}</span>
              <code className="keymap-row-id">{row.command}</code>
            </div>
            <div className="keymap-row-keys">
              {row.bindings.length === 0 && <span className="keymap-unbound">unbound</span>}
              {row.bindings.map((b) => (
                <BindingChip key={`${b.key}:${b.order}`} binding={b} all={bindings} />
              ))}
            </div>
            <div className="keymap-row-actions">
              <button
                type="button"
                onClick={() =>
                  setRecording({
                    command: row.command,
                    // Rebinding replaces the best default; without this the old
                    // key would keep working alongside the new one.
                    replaces: row.bindings.find((b) => b.source === 'default')
                      ? { key: row.bindings[0].key, command: row.command }
                      : undefined,
                  })
                }
              >
                {row.bindings.length ? 'Rebind' : 'Bind'}
              </button>
              {row.bindings.some((b) => b.source === 'default') && (
                <button
                  type="button"
                  onClick={() => {
                    const target = row.bindings.find((b) => b.source === 'default')!;
                    void disableKeybinding(target.key, row.command);
                  }}
                >
                  Unbind
                </button>
              )}
              {isCommandCustomized(row.command) && (
                <button type="button" onClick={() => void resetKeybindings(row.command)}>
                  Reset
                </button>
              )}
            </div>
          </div>
        ))}
        {visible.length === 0 && <div className="keymap-empty">No commands match.</div>}
      </div>
    </div>
  );
}
