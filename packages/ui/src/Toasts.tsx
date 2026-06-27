import { useSyncExternalStore } from 'react';
import { toastsStore, type Toast } from '@horrible/core';

function ToastCard({ t }: { t: Toast }) {
  const icon = {
    info: '💡',
    success: '🟢',
    warning: '⚠️',
    error: '🚨',
  }[t.type];

  return (
    <div className={`toast-card toast-${t.type}`}>
      <span className="toast-icon">{icon}</span>
      <div className="toast-content">
        <div className="toast-title">{t.title}</div>
        <div className="toast-message">{t.message}</div>
      </div>
      <button className="toast-close" onClick={() => toastsStore.remove(t.id)}>
        ×
      </button>
    </div>
  );
}

export function Toasts() {
  const toasts = useSyncExternalStore(
    toastsStore.subscribe,
    toastsStore.getToasts,
    toastsStore.getToasts
  );

  if (toasts.length === 0) return null;

  return (
    <div className="toasts-container">
      {toasts.map((t) => (
        <ToastCard key={t.id} t={t} />
      ))}
    </div>
  );
}
