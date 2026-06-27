export interface Toast {
  id: string;
  type: 'info' | 'success' | 'warning' | 'error';
  title: string;
  message: string;
  duration?: number;
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
  add(type: Toast['type'], title: string, message: string, duration = 4000): void {
    const id = Math.random().toString(36).substring(2, 9);
    toasts = [...toasts, { id, type, title, message, duration }];
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
