/**
 * The minibuffer strip: the bottom-most row of the frame, under the bottom dock.
 *
 * Three states, in priority order:
 *
 * 1. **Prompt** — a `dialogs.prompt()` is pending, so the minibuffer serves it
 *    inline (editor Save As lands here). Wins over everything: something is
 *    waiting on an answer.
 * 2. **Input** — `alt+x`, slash commands with live completions.
 * 3. **Status** — idle: workspace, focused pane, and the echo area.
 *
 * Matching and command resolution live in core/minibuffer.ts; this is the
 * rendering and the key handling.
 */
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  dialogsStore,
  findArea,
  layoutStore,
  matchCommands,
  minibuffer,
  resolveView,
  workspaceStore,
} from '@horrible/core';

/** The pending prompt, if any — the minibuffer renders these instead of a modal. */
function usePendingPrompt() {
  const [dialog, setDialog] = useState(() => dialogsStore.getActive());
  useEffect(() => dialogsStore.subscribe(setDialog), []);
  return dialog?.kind === 'prompt' ? dialog : null;
}

/** "workspace › pane" — what the status line says when nothing else is happening. */
function useStatusText(): string {
  const layout = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const workspaces = useSyncExternalStore(workspaceStore.subscribe, workspaceStore.getSnapshot);
  const workspace =
    workspaces.workspaces.find((w) => w.id === layout.workspaceId)?.name ?? layout.workspaceId;
  const area = layout.frame.focusedAreaId
    ? findArea(layout.frame.center, layout.frame.focusedAreaId)
    : null;
  const pane = area?.tabs[area.activeTab];
  const paneTitle = pane ? (resolveView(pane.viewId)?.title ?? pane.viewId) : null;
  if (!workspace) return '';
  return paneTitle ? `${workspace} › ${paneTitle}` : workspace;
}

export function Minibuffer() {
  const state = useSyncExternalStore(minibuffer.subscribe, minibuffer.getSnapshot);
  const prompt = usePendingPrompt();
  const status = useStatusText();
  const inputRef = useRef<HTMLInputElement>(null);
  const [promptValue, setPromptValue] = useState('');

  // Seed the field each time a new prompt arrives, and take focus.
  useEffect(() => {
    if (!prompt) return;
    setPromptValue(prompt.defaultValue ?? '');
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [prompt]);

  useEffect(() => {
    if (state.open) inputRef.current?.focus();
  }, [state.open]);

  if (prompt) {
    const settle = (value: string | null) => dialogsStore.resolvePrompt(prompt.id, value);
    return (
      <div className="frame-minibuffer frame-minibuffer--prompt">
        <label className="frame-minibuffer-label" htmlFor="minibuffer-input">
          {prompt.title}
        </label>
        <input
          id="minibuffer-input"
          ref={inputRef}
          className="frame-minibuffer-input"
          value={promptValue}
          placeholder={prompt.placeholder}
          onChange={(e) => setPromptValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              settle(promptValue);
            } else if (e.key === 'Escape') {
              e.preventDefault();
              settle(null);
            }
            // Every other key stays here rather than reaching global keybindings.
            e.stopPropagation();
          }}
        />
        {prompt.message && <span className="frame-minibuffer-hint">{prompt.message}</span>}
        <button className="frame-minibuffer-btn" onClick={() => settle(promptValue)}>
          {prompt.confirmLabel ?? 'OK'}
        </button>
        <button className="frame-minibuffer-btn" onClick={() => settle(null)}>
          {prompt.cancelLabel ?? 'Cancel'}
        </button>
      </div>
    );
  }

  if (state.open) {
    const matches = matchCommands(state.query);
    return (
      <div className="frame-minibuffer frame-minibuffer--input">
        <span className="frame-minibuffer-label">M-x</span>
        <input
          id="minibuffer-input"
          ref={inputRef}
          className="frame-minibuffer-input"
          value={state.query}
          placeholder="/save, /find, or search commands…"
          onChange={(e) => minibuffer.setQuery(e.target.value)}
          onBlur={() => minibuffer.close()}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              void minibuffer.submit();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              minibuffer.close();
            } else if (e.key === 'Tab' && matches[0]) {
              // Complete to the best match's slash name, emacs-style.
              e.preventDefault();
              minibuffer.setQuery(`/${matches[0].slash ?? matches[0].id}`);
            }
            e.stopPropagation();
          }}
        />
        <div className="frame-minibuffer-matches">
          {matches.map((c, i) => (
            <button
              key={c.id}
              className={`frame-minibuffer-match${i === 0 ? ' frame-minibuffer-match--best' : ''}`}
              // onMouseDown, not onClick: the input's onBlur would close the
              // strip first and the click would never land.
              onMouseDown={(e) => {
                e.preventDefault();
                minibuffer.setQuery(c.slash ? `/${c.slash}` : c.id);
                void minibuffer.submit();
              }}
            >
              {c.slash ? <code>/{c.slash}</code> : null} {c.title}
            </button>
          ))}
          {matches.length === 0 && <span className="frame-minibuffer-hint">No match</span>}
        </div>
      </div>
    );
  }

  return (
    <div className="frame-minibuffer frame-minibuffer--status">
      <span className="frame-minibuffer-status">{status}</span>
      {state.echo && (
        <span
          className={`frame-minibuffer-echo${
            state.echo.tone === 'error' ? ' frame-minibuffer-echo--error' : ''
          }`}
        >
          {state.echo.text}
        </span>
      )}
      <button
        className="frame-minibuffer-btn frame-minibuffer-mx"
        title="Run a command (alt+x)"
        onClick={() => minibuffer.open('/')}
      >
        M-x
      </button>
    </div>
  );
}
