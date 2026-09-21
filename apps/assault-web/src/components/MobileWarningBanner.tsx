import { useState, useEffect } from 'react';

export function MobileWarningBanner() {
  const [isMobile, setIsMobile] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const hasTouch =
      'ontouchstart' in window ||
      navigator.maxTouchPoints > 0 ||
      /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
    setIsMobile(hasTouch);
  }, []);

  if (!isMobile || dismissed) return null;

  return (
    <div
      style={{
        background: 'linear-gradient(90deg, rgba(234, 88, 12, 0.95), rgba(194, 65, 12, 0.95))',
        color: '#ffffff',
        padding: '0.6rem 1rem',
        fontSize: '0.82rem',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        boxShadow: '0 2px 10px rgba(0,0,0,0.5)',
        position: 'relative',
        zIndex: 9999,
        fontWeight: 500,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <span style={{ fontSize: '1.1rem' }}>⚠️</span>
        <span>
          <strong>Desktop Hardware Recommended:</strong> HorribleAssault requires keyboard (WASD) and pointer-lock mouse aiming. Touch controls are currently in preview.
        </span>
      </div>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        style={{
          background: 'rgba(0,0,0,0.25)',
          border: '1px solid rgba(255,255,255,0.3)',
          color: '#ffffff',
          borderRadius: 4,
          padding: '0.2rem 0.6rem',
          cursor: 'pointer',
          fontWeight: 600,
          fontSize: '0.75rem',
        }}
      >
        Dismiss
      </button>
    </div>
  );
}
