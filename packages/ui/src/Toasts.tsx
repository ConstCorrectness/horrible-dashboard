import { useState, useSyncExternalStore } from 'react';
import { toastsStore, type Toast } from '@horrible/core';

/**
 * The URL a toast wants the user to carry somewhere by hand, shown as selectable
 * text plus a Copy button.
 *
 * Deliberately not a link: this renders on the path where opening a link has
 * already failed, so another one would repeat the dead end. Clipboard writes can
 * themselves be refused (no permission, insecure context), and the URL stays
 * on screen and selectable either way.
 */
function CopyUrl({ url }: { url: string }) {
  const [copied, setCopied] = useState<boolean | null>(null);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="toast-copy">
      <code className="toast-copy-url">{url}</code>
      <button type="button" className="toast-copy-btn" onClick={() => void copy()}>
        {copied === true ? 'Copied' : copied === false ? 'Select and copy' : 'Copy link'}
      </button>
    </div>
  );
}

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
        {t.copyUrl && <CopyUrl url={t.copyUrl} />}
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
    toastsStore.getToasts,
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
