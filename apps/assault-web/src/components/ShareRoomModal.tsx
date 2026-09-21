import { useState } from 'react';

export interface ShareRoomModalProps {
  room: string;
  map: string;
  onClose: () => void;
}

export function ShareRoomModal({ room, map, onClose }: ShareRoomModalProps) {
  const [copied, setCopied] = useState(false);
  const shareUrl = `${window.location.origin}/#room=${encodeURIComponent(room)}&map=${encodeURIComponent(map)}`;

  const copyToClipboard = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(shareUrl);
      } else {
        const input = document.createElement('input');
        input.value = shareUrl;
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        document.body.removeChild(input);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // Fallback
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(4px)',
        zIndex: 10000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '1rem',
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: '100%',
          maxWidth: 480,
          backgroundColor: '#111620',
          border: '1px solid #2a3447',
          borderRadius: 8,
          padding: '1.5rem',
          boxShadow: '0 20px 50px rgba(0, 0, 0, 0.6)',
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ fontSize: '1.1rem', fontWeight: 700, color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span>🔗</span> Share Match Invite
          </h3>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#94a3b8',
              fontSize: '1.2rem',
              cursor: 'pointer',
            }}
          >
            ✕
          </button>
        </div>

        <p style={{ fontSize: '0.85rem', color: '#94a3b8', lineHeight: 1.4 }}>
          Send this link to dashboard users or guests. Anyone can click to instantly deploy into this match with zero install!
        </p>

        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
          <input
            type="text"
            readOnly
            value={shareUrl}
            style={{
              flex: 1,
              backgroundColor: '#0b0e14',
              border: '1px solid #334155',
              borderRadius: 6,
              color: '#38bdf8',
              padding: '0.6rem 0.8rem',
              fontSize: '0.82rem',
              fontFamily: 'monospace',
              outline: 'none',
            }}
            onClick={(e) => (e.target as HTMLInputElement).select()}
          />
          <button
            type="button"
            onClick={copyToClipboard}
            style={{
              backgroundColor: copied ? '#10b981' : '#2563eb',
              color: '#ffffff',
              border: 'none',
              borderRadius: 6,
              padding: '0.6rem 1.2rem',
              fontSize: '0.85rem',
              fontWeight: 600,
              cursor: 'pointer',
              transition: 'background-color 0.15s ease',
              whiteSpace: 'nowrap',
            }}
          >
            {copied ? '✓ Copied!' : 'Copy Link'}
          </button>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.75rem', color: '#64748b', borderTop: '1px solid #1e293b', paddingTop: '0.8rem', marginTop: '0.5rem' }}>
          <span>Room: <code>{room}</code></span>
          <span>Map: <code>{map}</code></span>
        </div>
      </div>
    </div>
  );
}
