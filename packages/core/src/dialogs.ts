/**
 * In-app modal dialogs — the promise-based replacement for the browser's
 * `window.prompt`/`window.confirm`, which are ugly, unstyled, and block the
 * whole tab. A module (or the shell) calls `dialogs.prompt(...)` /
 * `dialogs.confirm(...)` and awaits the result; the `<Dialogs />` component in
 * packages/ui renders the active dialog. Dialogs queue, so two requests in
 * flight show one at a time rather than clobbering each other.
 *
 * Pairs with the toast system ([[toasts]]): dialogs ask, toasts inform.
 */

export interface PromptOptions {
  title: string;
  /** Optional secondary line under the title. */
  message?: string;
  /** Pre-filled input value. */
  defaultValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
}

export interface ConfirmOptions {
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Render the confirm button as destructive (red). */
  danger?: boolean;
}

interface ActivePrompt extends PromptOptions {
  id: string;
  kind: 'prompt';
  resolve: (value: string | null) => void;
}

interface ActiveConfirm extends ConfirmOptions {
  id: string;
  kind: 'confirm';
  resolve: (value: boolean) => void;
}

export type ActiveDialog = ActivePrompt | ActiveConfirm;

const listeners = new Set<(dialog: ActiveDialog | null) => void>();
let queue: ActiveDialog[] = [];

function emit(): void {
  const head = queue[0] ?? null;
  listeners.forEach((l) => l(head));
}

function newId(): string {
  return Math.random().toString(36).substring(2, 9);
}

function enqueue(dialog: ActiveDialog): void {
  queue = [...queue, dialog];
  emit();
}

function settle(id: string): void {
  queue = queue.filter((d) => d.id !== id);
  emit();
}

export const dialogsStore = {
  subscribe(listener: (dialog: ActiveDialog | null) => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  /** The dialog to render right now (head of the queue), or null. */
  getActive(): ActiveDialog | null {
    return queue[0] ?? null;
  },

  /** Ask for a line of text. Resolves to the string, or null if cancelled. */
  prompt(options: PromptOptions): Promise<string | null> {
    return new Promise((resolve) => {
      enqueue({ ...options, id: newId(), kind: 'prompt', resolve });
    });
  },

  /** Ask a yes/no question. Resolves true if confirmed, false otherwise. */
  confirm(options: ConfirmOptions): Promise<boolean> {
    return new Promise((resolve) => {
      enqueue({ ...options, id: newId(), kind: 'confirm', resolve });
    });
  },

  /** Settle a prompt dialog with the entered value (or null on cancel). */
  resolvePrompt(id: string, value: string | null): void {
    const dialog = queue.find((d) => d.id === id);
    if (dialog?.kind === 'prompt') {
      settle(id);
      dialog.resolve(value);
    }
  },

  /** Settle a confirm dialog with the user's choice. */
  resolveConfirm(id: string, value: boolean): void {
    const dialog = queue.find((d) => d.id === id);
    if (dialog?.kind === 'confirm') {
      settle(id);
      dialog.resolve(value);
    }
  },
};

/** Convenience helpers mirroring the global functions they replace. */
export const dialogs = {
  prompt: dialogsStore.prompt.bind(dialogsStore),
  confirm: dialogsStore.confirm.bind(dialogsStore),
};
