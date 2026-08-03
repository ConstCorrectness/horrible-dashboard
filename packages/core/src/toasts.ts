export interface Toast {
  id: string;
  type: 'info' | 'success' | 'warning' | 'error';
  title: string;
  message: string;
  duration?: number;
  /**
   * A URL the user may need to carry somewhere by hand — rendered as selectable
   * text with a Copy button. Set by the "nothing could open this" path
   * (`onExternalOpenFailed`), where a clickable link is precisely what has already
   * been proven not to work, so offering another one would be the same dead end.
   */
  copyUrl?: string;
}

const listeners = new Set<(toasts: Toast[]) => void>();
let toasts: Toast[] = [];

function emit(): void {
  listeners.forEach((l) => l(toasts));
}

export const toastsStore = {
  subscribe(listener: (toasts: Toast[]) => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  getToasts(): Toast[] {
    return toasts;
  },
  add(
    type: Toast['type'],
    title: string,
    message: string,
    duration = 4000,
    extra: Pick<Toast, 'copyUrl'> = {},
  ): void {
    const id = Math.random().toString(36).substring(2, 9);
    toasts = [...toasts, { id, type, title, message, duration, ...extra }];
    emit();
    if (duration > 0) {
      setTimeout(() => {
        this.remove(id);
      }, duration);
    }
  },
  remove(id: string): void {
    toasts = toasts.filter((t) => t.id !== id);
    emit();
  },
};
