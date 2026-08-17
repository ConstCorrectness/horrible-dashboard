import { useEffect, useState } from 'react';
import type { MediaNetworkInsights } from './useClubhouseVoice';

interface MediaInsightsModalProps {
  isOpen: boolean;
  onClose: () => void;
  getInsights: () => MediaNetworkInsights;
  channelName: string | null;
}

export function MediaInsightsModal({
  isOpen,
  onClose,
  getInsights,
  channelName,
}: MediaInsightsModalProps) {
  const [insights, setInsights] = useState<MediaNetworkInsights>(getInsights());

  useEffect(() => {
    if (!isOpen) return;
    setInsights(getInsights());
    const interval = setInterval(() => {
      setInsights(getInsights());
    }, 1000);
    return () => clearInterval(interval);
  }, [isOpen, getInsights]);

  if (!isOpen) return null;

  const isConnected = insights.webrtcState === 'CONNECTED';

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(4px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9999,
        padding: '1rem',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          background: '#161922',
          border: '1px solid rgba(255, 255, 255, 0.1)',
          borderRadius: '12px',
          width: '100%',
          maxWidth: '680px',
          maxHeight: '85vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 20px 40px rgba(0, 0, 0, 0.5)',
          overflow: 'hidden',
        }}
      >
        {/* Modal Header */}
        <div
          style={{
            padding: '1rem 1.25rem',
            background: '#1b1f2b',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            <span style={{ fontSize: '1.25rem' }}>📡</span>
            <div>
              <h3 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700, color: '#f1f5f9' }}>
                Voice & Media Network Insights
              </h3>
              <p style={{ margin: 0, fontSize: '0.72rem', color: '#94a3b8' }}>
                Real-time transport protocols, IP routing, Agora RTC, PubNub signaling & audio telemetry
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#94a3b8',
              fontSize: '1.2rem',
              cursor: 'pointer',
              padding: '0.2rem 0.5rem',
              borderRadius: '4px',
            }}
          >
            ✕
          </button>
        </div>

        {/* Modal Body */}
        <div
          style={{
            padding: '1.25rem',
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: '1rem',
          }}
        >
          {/* Status Bar */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              background: isConnected ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
              border: `1px solid ${isConnected ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`,
              borderRadius: '8px',
              padding: '0.6rem 0.9rem',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span
                style={{
                  width: '10px',
                  height: '10px',
                  borderRadius: '50%',
                  backgroundColor: isConnected ? '#10b981' : '#ef4444',
                  boxShadow: `0 0 8px ${isConnected ? '#10b981' : '#ef4444'}`,
                }}
              />
              <span style={{ fontSize: '0.8rem', fontWeight: 700, color: isConnected ? '#34d399' : '#f87171' }}>
                {isConnected ? 'LIVE MEDIA PIPELINE ACTIVE' : 'DISCONNECTED / IDLE'}
              </span>
            </div>
            <span style={{ fontSize: '0.75rem', color: '#94a3b8', fontFamily: 'monospace' }}>
              Channel: {channelName || 'None'}
            </span>
          </div>

          {/* 1. Voice RTC & Media Transport */}
          <div
            style={{
              background: 'rgba(255, 255, 255, 0.03)',
              border: '1px solid rgba(255, 255, 255, 0.06)',
              borderRadius: '8px',
              padding: '0.85rem',
              display: 'flex',
              flexDirection: 'column',
              gap: '0.6rem',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#38bdf8' }}>
                🎙️ Real-Time Voice Transport (WebRTC / Agora SD-RTN)
              </span>
              <span
                style={{
                  fontSize: '0.65rem',
                  background: 'rgba(56, 189, 248, 0.15)',
                  color: '#38bdf8',
                  padding: '2px 6px',
                  borderRadius: '4px',
                  fontWeight: 700,
                }}
              >
                {insights.transportProtocol}
              </span>
            </div>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
                gap: '0.5rem',
                marginTop: '0.2rem',
              }}
            >
              <div style={statBoxStyle}>
                <span style={statLabelStyle}>Latency (RTT)</span>
                <span style={{ ...statValueStyle, color: insights.rttMs > 100 ? '#fbbf24' : '#34d399' }}>
                  {insights.rttMs > 0 ? `${insights.rttMs} ms` : '~30 ms (Est.)'}
                </span>
              </div>
              <div style={statBoxStyle}>
                <span style={statLabelStyle}>Uplink Bitrate</span>
                <span style={statValueStyle}>{insights.sendBitrateKbps || 48} kbps</span>
              </div>
              <div style={statBoxStyle}>
                <span style={statLabelStyle}>Downlink Bitrate</span>
                <span style={statValueStyle}>{insights.recvBitrateKbps || 96} kbps</span>
              </div>
              <div style={statBoxStyle}>
                <span style={statLabelStyle}>Audio Codec</span>
                <span style={{ ...statValueStyle, fontSize: '0.72rem' }}>{insights.codec}</span>
              </div>
            </div>

            <div style={{ fontSize: '0.72rem', color: '#94a3b8', marginTop: '0.2rem' }}>
              <strong>RTC Edge Gateways & Domains:</strong>
              <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginTop: '0.25rem' }}>
                {insights.rtcDomains.map((d) => (
                  <span
                    key={d}
                    style={{
                      fontSize: '0.68rem',
                      fontFamily: 'monospace',
                      background: 'rgba(0,0,0,0.3)',
                      padding: '2px 6px',
                      borderRadius: '4px',
                      color: '#cbd5e1',
                    }}
                  >
                    {d}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* 2. Real-Time Signaling & Presence */}
          <div
            style={{
              background: 'rgba(255, 255, 255, 0.03)',
              border: '1px solid rgba(255, 255, 255, 0.06)',
              borderRadius: '8px',
              padding: '0.85rem',
              display: 'flex',
              flexDirection: 'column',
              gap: '0.6rem',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#a78bfa' }}>
                ⚡ PubSub Signaling & Presence (PubNub)
              </span>
              <span
                style={{
                  fontSize: '0.65rem',
                  background: 'rgba(167, 139, 250, 0.15)',
                  color: '#c4b5fd',
                  padding: '2px 6px',
                  borderRadius: '4px',
                  fontWeight: 700,
                }}
              >
                {insights.pubnubProtocol}
              </span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.5rem' }}>
              <div style={statBoxStyle}>
                <span style={statLabelStyle}>Signaling Origin</span>
                <span style={{ ...statValueStyle, fontSize: '0.72rem' }}>{insights.pubnubOrigin}</span>
              </div>
              <div style={statBoxStyle}>
                <span style={statLabelStyle}>Keepalive Heartbeat</span>
                <span style={statValueStyle}>{insights.heartbeatIntervalS}s interval</span>
              </div>
            </div>

            <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
              <strong>Subscribed Presence Channels:</strong>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', marginTop: '0.25rem' }}>
                {insights.pubnubChannels.length > 0 ? (
                  insights.pubnubChannels.map((c) => (
                    <span
                      key={c}
                      style={{
                        fontSize: '0.68rem',
                        fontFamily: 'monospace',
                        background: 'rgba(0,0,0,0.3)',
                        padding: '2px 6px',
                        borderRadius: '4px',
                        color: '#a78bfa',
                      }}
                    >
                      {c}
                    </span>
                  ))
                ) : (
                  <span style={{ fontSize: '0.68rem', color: '#64748b' }}>No active channel subscriptions</span>
                )}
              </div>
            </div>
          </div>

          {/* 3. REST API & CDN Infrastructure */}
          <div
            style={{
              background: 'rgba(255, 255, 255, 0.03)',
              border: '1px solid rgba(255, 255, 255, 0.06)',
              borderRadius: '8px',
              padding: '0.85rem',
              display: 'flex',
              flexDirection: 'column',
              gap: '0.5rem',
            }}
          >
            <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#f59e0b' }}>
              🌐 REST API Gateways & Content Delivery (CDN)
            </span>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', fontSize: '0.72rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.04)', paddingBottom: '3px' }}>
                <span style={{ color: '#94a3b8' }}>Clubhouse API Gateway:</span>
                <span style={{ fontFamily: 'monospace', color: '#fde68a' }}>{insights.apiGateway}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.04)', paddingBottom: '3px' }}>
                <span style={{ color: '#94a3b8' }}>Avatar & Media CDN:</span>
                <span style={{ fontFamily: 'monospace', color: '#fde68a' }}>{insights.mediaCdn}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#94a3b8' }}>Backend Dashboard Bridge:</span>
                <span style={{ fontFamily: 'monospace', color: '#fde68a' }}>{insights.backendBridge}</span>
              </div>
            </div>
          </div>

          {/* 4. Local AI Pipeline & Audio Graph */}
          <div
            style={{
              background: 'rgba(255, 255, 255, 0.03)',
              border: '1px solid rgba(255, 255, 255, 0.06)',
              borderRadius: '8px',
              padding: '0.85rem',
              display: 'flex',
              flexDirection: 'column',
              gap: '0.5rem',
            }}
          >
            <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#ec4899' }}>
              🧠 Local AI & WebAudio Pipeline
            </span>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.5rem', fontSize: '0.72rem' }}>
              <div style={statBoxStyle}>
                <span style={statLabelStyle}>Speech-to-Text (STT)</span>
                <span style={{ ...statValueStyle, fontSize: '0.72rem' }}>{insights.sttEndpoint}</span>
              </div>
              <div style={statBoxStyle}>
                <span style={statLabelStyle}>Local LLM Engine</span>
                <span style={{ ...statValueStyle, fontSize: '0.72rem' }}>{insights.llmEndpoint}</span>
              </div>
              <div style={statBoxStyle}>
                <span style={statLabelStyle}>AudioContext Sample Rate</span>
                <span style={statValueStyle}>{insights.sampleRateHz.toLocaleString()} Hz ({insights.audioChannels}ch)</span>
              </div>
              <div style={statBoxStyle}>
                <span style={statLabelStyle}>Text-to-Speech (TTS)</span>
                <span style={{ ...statValueStyle, fontSize: '0.72rem' }}>{insights.ttsEngine}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Modal Footer */}
        <div
          style={{
            padding: '0.75rem 1.25rem',
            background: '#1b1f2b',
            borderTop: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex',
            justifyContent: 'flex-end',
          }}
        >
          <button
            className="ch-btn-action"
            onClick={onClose}
            style={{
              padding: '0.4rem 1rem',
              borderRadius: '6px',
              fontSize: '0.8rem',
              background: '#334155',
              color: 'white',
              border: 'none',
              cursor: 'pointer',
            }}
          >
            Close Diagnostics
          </button>
        </div>
      </div>
    </div>
  );
}

const statBoxStyle: React.CSSProperties = {
  background: 'rgba(0, 0, 0, 0.25)',
  padding: '0.45rem 0.6rem',
  borderRadius: '6px',
  display: 'flex',
  flexDirection: 'column',
  gap: '2px',
};

const statLabelStyle: React.CSSProperties = {
  fontSize: '0.65rem',
  color: '#94a3b8',
  fontWeight: 600,
};

const statValueStyle: React.CSSProperties = {
  fontSize: '0.8rem',
  color: '#f1f5f9',
  fontWeight: 700,
  fontFamily: 'monospace',
};
