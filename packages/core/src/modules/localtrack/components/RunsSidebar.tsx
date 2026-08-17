import { useState } from 'react';
import { getRunColor, useLocalTrackStore } from '../store';
import { LocalTrackIcon } from './LocalTrackIcon';

export function RunsSidebar({
  collapsed,
  onToggleCollapse,
}: {
  collapsed: boolean;
  onToggleCollapse: () => void;
}) {
  const {
    projects,
    activeProjectId,
    setActiveProject,
    createNewProject,
    runs,
    selectedRunIds,
    toggleRunSelection,
    selectAllRuns,
    deselectAllRuns,
    openRunDetails,
    loadRuns,
  } = useLocalTrackStore();

  const [search, setSearch] = useState('');
  const [showNewProjModal, setShowNewProjModal] = useState(false);
  const [newProjName, setNewProjName] = useState('');

  const filteredRuns = runs.filter((r) =>
    r.name.toLowerCase().includes(search.toLowerCase()) ||
    r.tags.some((t) => t.toLowerCase().includes(search.toLowerCase()))
  );

  const handleCreateProj = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProjName.trim()) return;
    await createNewProject(newProjName.trim());
    setNewProjName('');
    setShowNewProjModal(false);
  };

  if (collapsed) {
    return (
      <div
        style={{
          width: 44,
          borderRight: '1px solid var(--border-dim, #30363d)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: '12px 4px',
          gap: 12,
          background: 'var(--bg-secondary, #161b22)',
        }}
      >
        <button
          onClick={onToggleCollapse}
          title="Expand Runs Sidebar"
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-secondary, #8b949e)',
            cursor: 'pointer',
            padding: 6,
            borderRadius: 4,
          }}
        >
          <LocalTrackIcon size={16} />
        </button>
        <div style={{ writingMode: 'vertical-lr', fontSize: 11, color: 'var(--text-dim, #8b949e)', letterSpacing: 1 }}>
          RUNS ({runs.length})
        </div>
      </div>
    );
  }

  return (
    <aside
      style={{
        width: 280,
        minWidth: 280,
        borderRight: '1px solid var(--border-dim, #30363d)',
        background: 'var(--bg-secondary, #161b22)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        userSelect: 'none',
      }}
    >
      {/* Sidebar Header: Project Switcher */}
      <div
        style={{
          padding: '12px 14px',
          borderBottom: '1px solid var(--border-dim, #30363d)',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <LocalTrackIcon size={16} />
            <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-dim, #8b949e)' }}>
              Project
            </span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={() => setShowNewProjModal(true)}
              title="New Project"
              style={{
                background: 'rgba(255,255,255,0.06)',
                border: '1px solid var(--border-dim, #30363d)',
                color: 'var(--text-primary, #c9d1d9)',
                borderRadius: 4,
                padding: '2px 6px',
                fontSize: 11,
                cursor: 'pointer',
              }}
            >
              + New
            </button>
            <button
              onClick={onToggleCollapse}
              title="Collapse Sidebar"
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--text-secondary, #8b949e)',
                cursor: 'pointer',
                padding: '2px 4px',
                fontSize: 11,
              }}
            >
              ◀
            </button>
          </div>
        </div>

        <select
          value={activeProjectId}
          onChange={(e) => setActiveProject(e.target.value)}
          style={{
            background: 'var(--bg-tertiary, #0d1117)',
            color: 'var(--text-primary, #c9d1d9)',
            border: '1px solid var(--border-dim, #30363d)',
            borderRadius: 6,
            padding: '6px 8px',
            fontSize: 13,
            fontWeight: 500,
            outline: 'none',
            cursor: 'pointer',
          }}
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.run_count})
            </option>
          ))}
        </select>
      </div>

      {/* Runs Controls */}
      <div
        style={{
          padding: '10px 14px',
          borderBottom: '1px solid var(--border-dim, #30363d)',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        <input
          type="text"
          placeholder="Filter runs by name/tag..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{
            background: 'var(--bg-tertiary, #0d1117)',
            color: 'var(--text-primary, #c9d1d9)',
            border: '1px solid var(--border-dim, #30363d)',
            borderRadius: 4,
            padding: '4px 8px',
            fontSize: 12,
            outline: 'none',
          }}
        />

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--text-dim, #8b949e)' }}>
            {selectedRunIds.size} of {runs.length} selected
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={selectAllRuns}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--accent, #58a6ff)',
                fontSize: 11,
                cursor: 'pointer',
                padding: 0,
              }}
            >
              All
            </button>
            <span style={{ color: 'var(--border-dim, #30363d)' }}>|</span>
            <button
              onClick={deselectAllRuns}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--text-dim, #8b949e)',
                fontSize: 11,
                cursor: 'pointer',
                padding: 0,
              }}
            >
              None
            </button>
            <span style={{ color: 'var(--border-dim, #30363d)' }}>|</span>
            <button
              onClick={() => loadRuns()}
              title="Refresh Runs"
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--text-dim, #8b949e)',
                fontSize: 11,
                cursor: 'pointer',
                padding: 0,
              }}
            >
              ⟳
            </button>
          </div>
        </div>
      </div>

      {/* Runs List */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 0' }}>
        {filteredRuns.length === 0 ? (
          <div style={{ padding: '24px 14px', textAlign: 'center', color: 'var(--text-dim, #8b949e)', fontSize: 12 }}>
            No runs found in this project.
          </div>
        ) : (
          filteredRuns.map((run, idx) => {
            const isSelected = selectedRunIds.has(run.id);
            const color = getRunColor(run.id, idx);
            const isRunning = run.status === 'running';
            const isFailed = run.status === 'failed';

            return (
              <div
                key={run.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  padding: '6px 12px',
                  gap: 8,
                  fontSize: 12,
                  cursor: 'pointer',
                  background: isSelected ? 'rgba(56, 139, 253, 0.08)' : 'transparent',
                  borderLeft: `3px solid ${isSelected ? color : 'transparent'}`,
                  transition: 'background 0.15s ease',
                }}
                onMouseEnter={(e) => {
                  if (!isSelected) e.currentTarget.style.background = 'rgba(255,255,255,0.03)';
                }}
                onMouseLeave={(e) => {
                  if (!isSelected) e.currentTarget.style.background = 'transparent';
                }}
              >
                {/* Eye toggle button */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleRunSelection(run.id);
                  }}
                  title={isSelected ? 'Hide from charts' : 'Show in charts'}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: isSelected ? color : 'var(--text-dim, #484f58)',
                    cursor: 'pointer',
                    fontSize: 14,
                    padding: 0,
                    width: 18,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  {isSelected ? '👁' : '👁‍🗨'}
                </button>

                {/* Color Dot & Run Name */}
                <div
                  onClick={() => openRunDetails(run)}
                  style={{
                    flex: 1,
                    display: 'flex',
                    flexDirection: 'column',
                    overflow: 'hidden',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        background: color,
                        flexShrink: 0,
                      }}
                    />
                    <span
                      style={{
                        fontWeight: 500,
                        color: 'var(--text-primary, #c9d1d9)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {run.name}
                    </span>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2, fontSize: 10, color: 'var(--text-dim, #8b949e)' }}>
                    <span
                      style={{
                        padding: '1px 4px',
                        borderRadius: 3,
                        fontSize: 9,
                        fontWeight: 600,
                        textTransform: 'uppercase',
                        background: isRunning
                          ? 'rgba(46, 204, 113, 0.2)'
                          : isFailed
                          ? 'rgba(231, 76, 60, 0.2)'
                          : 'rgba(52, 152, 219, 0.2)',
                        color: isRunning ? '#2ecc71' : isFailed ? '#e74c3c' : '#3498db',
                      }}
                    >
                      {run.status}
                    </span>
                    {run.duration_seconds > 0 && (
                      <span>{Math.round(run.duration_seconds)}s</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* New Project Modal */}
      {showNewProjModal && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
          }}
        >
          <form
            onSubmit={handleCreateProj}
            style={{
              background: 'var(--bg-primary, #0d1117)',
              border: '1px solid var(--border-dim, #30363d)',
              borderRadius: 8,
              padding: 20,
              width: 340,
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
              boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
            }}
          >
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary, #c9d1d9)' }}>
              Create New Project
            </div>
            <input
              type="text"
              placeholder="Project Name (e.g. llama3-lora-sft)"
              value={newProjName}
              onChange={(e) => setNewProjName(e.target.value)}
              autoFocus
              style={{
                background: 'var(--bg-secondary, #161b22)',
                border: '1px solid var(--border-dim, #30363d)',
                borderRadius: 4,
                padding: '8px 10px',
                color: 'var(--text-primary, #c9d1d9)',
                fontSize: 13,
                outline: 'none',
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button
                type="button"
                onClick={() => setShowNewProjModal(false)}
                style={{
                  background: 'none',
                  border: '1px solid var(--border-dim, #30363d)',
                  color: 'var(--text-secondary, #8b949e)',
                  borderRadius: 4,
                  padding: '6px 12px',
                  fontSize: 12,
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                style={{
                  background: 'var(--accent, #1f6feb)',
                  border: 'none',
                  color: '#fff',
                  borderRadius: 4,
                  padding: '6px 14px',
                  fontSize: 12,
                  fontWeight: 500,
                  cursor: 'pointer',
                }}
              >
                Create
              </button>
            </div>
          </form>
        </div>
      )}
    </aside>
  );
}
