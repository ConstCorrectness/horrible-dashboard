import { useEffect, useState } from 'react';
import { apiUrl } from '../../../origin';
import { fetchRunArtifacts } from '../api';
import { useLocalTrackStore } from '../store';
import type { RunArtifact } from '../types';
import { ChartPanel } from './ChartPanel';

export function RunDetailsModal() {
  const { selectedRunForDetails, closeRunDetails, panels } = useLocalTrackStore();
  const [activeTab, setActiveTab] = useState<'charts' | 'overview' | 'files'>('charts');
  const [artifacts, setArtifacts] = useState<RunArtifact[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);

  const run = selectedRunForDetails;

  useEffect(() => {
    if (!run) return;
    setLoadingFiles(true);
    fetchRunArtifacts(run.id)
      .then((res) => {
        setArtifacts(res);
        setLoadingFiles(false);
      })
      .catch(() => setLoadingFiles(false));
  }, [run]);

  if (!run) return null;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.7)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9999,
        backdropFilter: 'blur(3px)',
      }}
    >
      <div
        style={{
          background: 'var(--bg-primary, #0d1117)',
          border: '1px solid var(--border-dim, #30363d)',
          borderRadius: 8,
          width: '90vw',
          maxWidth: 1080,
          height: '85vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 16px 48px rgba(0,0,0,0.7)',
          overflow: 'hidden',
        }}
      >
        {/* Modal Header */}
        <div
          style={{
            padding: '14px 20px',
            borderBottom: '1px solid var(--border-dim, #30363d)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'var(--bg-secondary, #161b22)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary, #c9d1d9)' }}>
              {run.name}
            </span>
            <span
              style={{
                padding: '2px 8px',
                borderRadius: 4,
                fontSize: 10,
                fontWeight: 600,
                textTransform: 'uppercase',
                background:
                  run.status === 'running'
                    ? 'rgba(46, 204, 113, 0.2)'
                    : run.status === 'failed'
                    ? 'rgba(231, 76, 60, 0.2)'
                    : 'rgba(52, 152, 219, 0.2)',
                color:
                  run.status === 'running'
                    ? '#2ecc71'
                    : run.status === 'failed'
                    ? '#e74c3c'
                    : '#3498db',
              }}
            >
              {run.status}
            </span>
            <span style={{ fontSize: 12, color: 'var(--text-dim, #8b949e)' }}>
              ID: {run.id}
            </span>
          </div>

          <button
            onClick={closeRunDetails}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-dim, #8b949e)',
              fontSize: 18,
              cursor: 'pointer',
            }}
          >
            ✕
          </button>
        </div>

        {/* Tab Navigation */}
        <div
          style={{
            display: 'flex',
            gap: 2,
            borderBottom: '1px solid var(--border-dim, #30363d)',
            padding: '0 20px',
            background: 'var(--bg-secondary, #161b22)',
          }}
        >
          {([
            { key: 'charts', label: 'Charts' },
            { key: 'overview', label: 'Overview' },
            { key: 'files', label: 'Artifacts' },
          ] as const).map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              style={{
                background: 'none',
                border: 'none',
                borderBottom: activeTab === tab.key ? '2px solid var(--accent, #58a6ff)' : '2px solid transparent',
                color: activeTab === tab.key ? 'var(--text-primary, #c9d1d9)' : 'var(--text-dim, #8b949e)',
                padding: '10px 16px',
                fontSize: 13,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab Contents */}
        <div style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
          {/* CHARTS TAB */}
          {activeTab === 'charts' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: 16 }}>
              {panels
                .filter((p) => p.chartType === 'line')
                .map((panel) => (
                  <div key={panel.id} style={{ height: 320 }}>
                    <ChartPanel panel={panel} />
                  </div>
                ))}
            </div>
          )}

          {/* OVERVIEW TAB */}
          {activeTab === 'overview' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              {/* General Metadata */}
              <div
                style={{
                  background: 'var(--bg-secondary, #161b22)',
                  border: '1px solid var(--border-dim, #30363d)',
                  borderRadius: 8,
                  padding: 16,
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary, #c9d1d9)', marginBottom: 12 }}>
                  Run Metadata
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, fontSize: 12 }}>
                  <div>
                    <span style={{ color: 'var(--text-dim, #8b949e)' }}>Project: </span>
                    <span style={{ color: 'var(--text-primary, #c9d1d9)', fontWeight: 500 }}>{run.project_id}</span>
                  </div>
                  <div>
                    <span style={{ color: 'var(--text-dim, #8b949e)' }}>Start Time: </span>
                    <span style={{ color: 'var(--text-primary, #c9d1d9)', fontWeight: 500 }}>{run.start_time || 'N/A'}</span>
                  </div>
                  <div>
                    <span style={{ color: 'var(--text-dim, #8b949e)' }}>Duration: </span>
                    <span style={{ color: 'var(--text-primary, #c9d1d9)', fontWeight: 500 }}>
                      {run.duration_seconds > 0 ? `${Math.round(run.duration_seconds)}s` : 'In progress'}
                    </span>
                  </div>
                  <div>
                    <span style={{ color: 'var(--text-dim, #8b949e)' }}>Tags: </span>
                    <span style={{ color: 'var(--text-primary, #c9d1d9)', fontWeight: 500 }}>
                      {run.tags.length ? run.tags.join(', ') : 'None'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Hyperparameters Config Table */}
              <div
                style={{
                  background: 'var(--bg-secondary, #161b22)',
                  border: '1px solid var(--border-dim, #30363d)',
                  borderRadius: 8,
                  padding: 16,
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary, #c9d1d9)', marginBottom: 12 }}>
                  Logged Hyperparameters (config)
                </div>
                {Object.keys(run.config).length === 0 ? (
                  <div style={{ color: 'var(--text-dim, #8b949e)', fontSize: 12 }}>No configuration logged.</div>
                ) : (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 8 }}>
                    {Object.entries(run.config).map(([k, v]) => (
                      <div
                        key={k}
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          padding: '6px 10px',
                          borderRadius: 4,
                          background: 'rgba(255,255,255,0.02)',
                          border: '1px solid rgba(255,255,255,0.04)',
                          fontSize: 12,
                        }}
                      >
                        <span style={{ color: 'var(--text-secondary, #8b949e)', fontWeight: 500 }}>{k}</span>
                        <span style={{ color: 'var(--text-primary, #c9d1d9)', fontFamily: 'monospace', fontWeight: 600 }}>
                          {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* System Info */}
              {Object.keys(run.system_info).length > 0 && (
                <div
                  style={{
                    background: 'var(--bg-secondary, #161b22)',
                    border: '1px solid var(--border-dim, #30363d)',
                    borderRadius: 8,
                    padding: 16,
                  }}
                >
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary, #c9d1d9)', marginBottom: 12 }}>
                    Hardware & System Info
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 8 }}>
                    {Object.entries(run.system_info).map(([k, v]) => (
                      <div
                        key={k}
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          padding: '6px 10px',
                          borderRadius: 4,
                          background: 'rgba(255,255,255,0.02)',
                          fontSize: 12,
                        }}
                      >
                        <span style={{ color: 'var(--text-secondary, #8b949e)' }}>{k}</span>
                        <span style={{ color: 'var(--text-primary, #c9d1d9)', fontWeight: 500 }}>{String(v)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* FILES TAB */}
          {activeTab === 'files' && (
            <div
              style={{
                background: 'var(--bg-secondary, #161b22)',
                border: '1px solid var(--border-dim, #30363d)',
                borderRadius: 8,
                padding: 16,
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary, #c9d1d9)', marginBottom: 12 }}>
                Run Artifacts & Outputs
              </div>

              {loadingFiles ? (
                <div style={{ color: 'var(--text-dim, #8b949e)', fontSize: 12 }}>Loading files...</div>
              ) : artifacts.length === 0 ? (
                <div style={{ color: 'var(--text-dim, #8b949e)', fontSize: 12 }}>
                  No artifacts saved for this run (e.g. config.json, trainer_state.json).
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {artifacts.map((art) => (
                    <div
                      key={art.id}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '10px 14px',
                        background: 'rgba(255,255,255,0.02)',
                        border: '1px solid rgba(255,255,255,0.05)',
                        borderRadius: 6,
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{ fontSize: 16 }}>📄</span>
                        <div style={{ display: 'flex', flexDirection: 'column' }}>
                          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary, #c9d1d9)' }}>
                            {art.filename}
                          </span>
                          <span style={{ fontSize: 11, color: 'var(--text-dim, #8b949e)' }}>
                            {art.size_bytes} bytes · {art.content_type} · {art.created_at}
                          </span>
                        </div>
                      </div>

                      <a
                        href={apiUrl(`/api/localtrack/runs/${encodeURIComponent(run.id)}/artifacts/${encodeURIComponent(art.id)}/download`)}
                        download={art.filename}
                        target="_blank"
                        rel="noreferrer"
                        style={{
                          background: 'var(--accent, #1f6feb)',
                          color: '#fff',
                          padding: '6px 12px',
                          borderRadius: 4,
                          fontSize: 12,
                          fontWeight: 500,
                          textDecoration: 'none',
                          cursor: 'pointer',
                        }}
                      >
                        Download ⬇
                      </a>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
