import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { dialogsStore, type ActiveDialog } from '@horrible/core';

function useActiveDialog(): ActiveDialog | null {
  return useSyncExternalStore(
    dialogsStore.subscribe,
    dialogsStore.getActive,
    dialogsStore.getActive,
  );
}

/** The text-input dialog: replaces window.prompt. */
function PromptDialog({ dialog }: { dialog: Extract<ActiveDialog, { kind: 'prompt' }> }) {
  const [value, setValue] = useState(dialog.defaultValue ?? '');
  const inputRef = useRef<HTMLInputElement>(null);

  // Re-seed and focus when a new prompt becomes active (the component is reused
  // across queued dialogs by React keying below).
  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.focus();
    input.select();
  }, []);

  const submit = () => dialogsStore.resolvePrompt(dialog.id, value);
  const cancel = () => dialogsStore.resolvePrompt(dialog.id, null);

  return (
    <form
      className="dialog-card"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="dialog-title">{dialog.title}</div>
      {dialog.message && <div className="dialog-message">{dialog.message}</div>}
      <input
        ref={inputRef}
        className="dialog-input"
        value={value}
        placeholder={dialog.placeholder}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault();
            cancel();
          }
        }}
      />
      <div className="dialog-actions">
        <button type="button" className="dialog-btn" onClick={cancel}>
          {dialog.cancelLabel ?? 'Cancel'}
        </button>
        <button type="submit" className="dialog-btn dialog-btn-primary">
          {dialog.confirmLabel ?? 'OK'}
        </button>
      </div>
    </form>
  );
}

/** The yes/no dialog: replaces window.confirm. */
function ConfirmDialog({ dialog }: { dialog: Extract<ActiveDialog, { kind: 'confirm' }> }) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    confirmRef.current?.focus();
  }, []);

  const confirm = () => dialogsStore.resolveConfirm(dialog.id, true);
  const cancel = () => dialogsStore.resolveConfirm(dialog.id, false);

  return (
    <div className="dialog-card">
      <div className="dialog-title">{dialog.title}</div>
      {dialog.message && <div className="dialog-message">{dialog.message}</div>}
      <div className="dialog-actions">
        <button type="button" className="dialog-btn" onClick={cancel}>
          {dialog.cancelLabel ?? 'Cancel'}
        </button>
        <button
          ref={confirmRef}
          type="button"
          className={`dialog-btn dialog-btn-primary${dialog.danger ? ' dialog-btn-danger' : ''}`}
          onClick={confirm}
        >
          {dialog.confirmLabel ?? 'OK'}
        </button>
      </div>
    </div>
  );
}

/** The multi-button dialog (e.g. Save / Don't Save / Cancel). */
function ChoiceDialog({ dialog }: { dialog: Extract<ActiveDialog, { kind: 'choice' }> }) {
  const primaryRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    primaryRef.current?.focus();
  }, []);

  const primaryIndex = dialog.buttons.findIndex((b) => b.primary);

  return (
    <div className="dialog-card">
      <div className="dialog-title">{dialog.title}</div>
      {dialog.message && <div className="dialog-message">{dialog.message}</div>}
      <div className="dialog-actions">
        {dialog.buttons.map((button, i) => (
          <button
            key={button.value}
            ref={i === primaryIndex ? primaryRef : undefined}
            type="button"
            className={`dialog-btn${button.primary ? ' dialog-btn-primary' : ''}${
              button.danger ? ' dialog-btn-danger' : ''
            }`}
            onClick={() => dialogsStore.resolveChoice(dialog.id, button.value)}
          >
            {button.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Dismiss the active dialog the way Esc / a backdrop click should — each kind
 * resolves with its own cancel value. */
function dismissActive(dialog: ActiveDialog): void {
  if (dialog.kind === 'prompt') dialogsStore.resolvePrompt(dialog.id, null);
  else if (dialog.kind === 'confirm') dialogsStore.resolveConfirm(dialog.id, false);
  else dialogsStore.resolveChoice(dialog.id, dialog.cancelValue ?? null);
}

export function Dialogs() {
  const dialog = useActiveDialog();

  // Escape always cancels the active dialog (confirm + prompt-outside-input).
  useEffect(() => {
    if (!dialog) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopPropagation();
      dismissActive(dialog);
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [dialog]);

  if (!dialog) return null;

  return (
    <div
      className="dialog-overlay"
      onMouseDown={(e) => {
        // Click on the backdrop (not the card) cancels.
        if (e.target !== e.currentTarget) return;
        dismissActive(dialog);
      }}
    >
      {dialog.kind === 'prompt' ? (
        <PromptDialog key={dialog.id} dialog={dialog} />
      ) : dialog.kind === 'confirm' ? (
        <ConfirmDialog key={dialog.id} dialog={dialog} />
      ) : (
        <ChoiceDialog key={dialog.id} dialog={dialog} />
      )}
    </div>
  );
}
