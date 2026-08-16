/**
 * Spotlight — `mod+k`. One surface for "ask, run, or open".
 *
 * This replaced the command palette rather than sitting beside it. The agent is
 * meant to be always available, and the honest way to do that is one keystroke
 * that takes anything, not a second overlay competing for the same keyboard: two
 * overlays means the user has to decide which one they want before they can
 * start typing.
 *
 * The result list is resolved by `spotlightResults` in core, so what appears and
 * in what order is unit-tested rather than argued about here.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  askAgent,
  bindingsFor,
  focusInstance,
  labelSpec,
  layoutStore,
  registry,
  spotlightResults,
  suppressNativeOverlays,
  toastsStore,
  tryParseSpec,
  useKeyContext,
  useKeymap,
  type KeyContext,
  type ResolvedBinding,
  findPaneAnywhere,
  type SpotlightItem,
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

export function Spotlight({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const [asking, setAsking] = useState<string | null>(null);
  const [answer, setAnswer] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const bindings = useKeymap();
  const ctx = useKeyContext();

  useEffect(() => {
    if (open) {
      setQuery('');
      setSelected(0);
      setAsking(null);
      setAnswer('');
      inputRef.current?.focus();
    }
  }, [open]);

  // A native child webview (the browser pane's overlay) is composited by the OS
  // above the HTML layer, so this would open *behind* it no matter what z-index
  // it carries. Claim suppression while open; the release un-hides it.
  useEffect(() => (open ? suppressNativeOverlays() : undefined), [open]);

  const items = useMemo(
    () =>
      open
        ? spotlightResults(query, layoutStore.getSnapshot().frame, (id) =>
            shortcutLabel(id, bindings, ctx),
          )
        : [],
    [open, query, bindings, ctx],
  );

  if (!open) return null;

  const ask = (prompt: string) => {
    // The spotlight stays open for an ask, unlike every other action: the answer
    // is the point, and closing to deliver it somewhere else would mean the user
    // has to go find it.
    setAsking(prompt);
    setAnswer('');
    void askAgent(prompt, {
      onToken: (delta) => setAnswer((a) => a + delta),
      onAnswer: (text) => setAnswer((a) => text || a),
      onError: (msg) => setAnswer(`Something went wrong: ${msg}`),
    });
  };

  const run = (index: number) => {
    const item: SpotlightItem | undefined = items[index];
    if (!item) return;
    switch (item.action.type) {
      case 'command':
        onClose();
        void registry.runCommand(item.action.commandId).catch((err: unknown) => {
          toastsStore.add('error', 'Command failed', String(err), 4000);
        });
        return;
      case 'focusPane': {
        onClose();
        const located = findPaneAnywhere(layoutStore.getSnapshot().frame, item.action.instanceId);
        // It closed between the list being built and the click. Nothing to do,
        // and nothing worth interrupting the user about.
        if (located) focusInstance(located);
        return;
      }
      case 'ask':
        ask(item.action.prompt);
        return;
    }
  };

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="palette spotlight" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          value={query}
          placeholder="Ask, run a command, or jump to a pane…"
          onChange={(e) => {
            setQuery(e.target.value);
            setSelected(0);
            setAsking(null);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose();
            if (e.key === 'Enter') run(selected);
            if (e.key === 'ArrowDown') {
              e.preventDefault();
              setSelected((s) => Math.min(s + 1, items.length - 1));
            }
            if (e.key === 'ArrowUp') {
              e.preventDefault();
              setSelected((s) => Math.max(s - 1, 0));
            }
          }}
        />
        {asking !== null ? (
          <div className="spotlight-answer">
            <p className="spotlight-asked">{asking}</p>
            <div className="spotlight-reply">{answer || 'Thinking…'}</div>
          </div>
        ) : (
          <ul>
            {items.map((item, i) => (
              <li
                key={item.key}
                className={`${i === selected ? 'selected' : ''} spotlight-${item.kind}`}
                onClick={() => run(i)}
              >
                <span className="spotlight-title">
                  {item.icon && (
                    <span className="spotlight-icon" aria-hidden="true">
                      {item.icon}
                    </span>
                  )}
                  {item.title}
                </span>
                {item.hint && <kbd>{item.hint}</kbd>}
              </li>
            ))}
            {items.length === 0 && <li className="empty">Type to search, or ask a question</li>}
          </ul>
        )}
      </div>
    </div>
  );
}
