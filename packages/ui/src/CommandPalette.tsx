import { useEffect, useRef, useState } from 'react';
import {
  bindingsFor,
  labelSpec,
  registry,
  suppressNativeOverlays,
  tryParseSpec,
  useKeyContext,
  useKeymap,
  type KeyContext,
  type ResolvedBinding,
} from '@horrible/core';

/**
 * The shortcut to show for a command: the one that would actually fire right now
 * (`bindingsFor` sorts live bindings first), rendered with this platform's
 * conventions. The palette used to print the raw command id here, which told the
 * user nothing about how to reach the command without opening the palette.
 */
function shortcutLabel(
  command: string,
  bindings: readonly ResolvedBinding[],
  ctx: KeyContext,
): string | null {
  const best: ResolvedBinding | undefined = bindingsFor(command, bindings, ctx)[0];
  if (!best) return null;
  const chord = tryParseSpec(best.key);
  return chord ? labelSpec(chord, { platform: ctx.platform }) : null;
}

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const bindings = useKeymap();
  const ctx = useKeyContext();

  useEffect(() => {
    if (open) {
      setQuery('');
      setSelected(0);
      inputRef.current?.focus();
    }
  }, [open]);

  // A native child webview (the browser pane's overlay) is composited by the OS
  // above the HTML layer, so the palette would open *behind* it no matter what
  // z-index it carries. Claim suppression while open; the release un-hides it.
  useEffect(() => (open ? suppressNativeOverlays() : undefined), [open]);

  if (!open) return null;

  const matches = registry.commands.filter((c) =>
    c.title.toLowerCase().includes(query.toLowerCase()),
  );

  const run = (index: number) => {
    const command = matches[index];
    onClose();
    if (command) void registry.runCommand(command.id);
  };

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          value={query}
          placeholder="Type a command…"
          onChange={(e) => {
            setQuery(e.target.value);
            setSelected(0);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose();
            if (e.key === 'Enter') run(selected);
            if (e.key === 'ArrowDown') setSelected((s) => Math.min(s + 1, matches.length - 1));
            if (e.key === 'ArrowUp') setSelected((s) => Math.max(s - 1, 0));
          }}
        />
        <ul>
          {matches.map((c, i) => {
            const shortcut = shortcutLabel(c.id, bindings, ctx);
            return (
              <li key={c.id} className={i === selected ? 'selected' : ''} onClick={() => run(i)}>
                <span>{c.title}</span>
                {shortcut ? <kbd>{shortcut}</kbd> : <code>{c.id}</code>}
              </li>
            );
          })}
          {matches.length === 0 && <li className="empty">No matching commands</li>}
        </ul>
      </div>
    </div>
  );
}
