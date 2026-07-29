import { useEffect, useRef, useSyncExternalStore } from 'react';
import { dialogsStore, type ActiveDialog } from '@horrible/core';

function useActiveDialog(): ActiveDialog | null {
  return useSyncExternalStore(
    dialogsStore.subscribe,
    dialogsStore.getActive,
    dialogsStore.getActive,
  );
}

/* The text-input dialog moved to the minibuffer (see Minibuffer.tsx). */

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

/**
 * Modal dialogs. `prompt` is deliberately **not** rendered here: the minibuffer
 * serves it inline along the bottom of the frame (emacs-style), and rendering it
 * in both places would show two competing inputs for one pending promise.
 * `confirm`/`choice` stay modal — a destructive Save / Don't Save / Cancel
 * should interrupt, not be a line you can ignore.
 */
export function Dialogs() {
  const active = useActiveDialog();
  const dialog = active?.kind === 'prompt' ? null : active;

  // Escape is handled by the shell's Escape ladder (rung 2), not here — see
  // packages/core/src/keymap/dispatch.ts.
  if (!dialog) return null;

  return (
    <div
      className="dialog-overlay"
      onMouseDown={(e) => {
        // Click on the backdrop (not the card) cancels.
        if (e.target !== e.currentTarget) return;
        dialogsStore.dismissActive();
      }}
    >
      {dialog.kind === 'confirm' ? (
        <ConfirmDialog key={dialog.id} dialog={dialog} />
      ) : (
        <ChoiceDialog key={dialog.id} dialog={dialog} />
      )}
    </div>
  );
}
