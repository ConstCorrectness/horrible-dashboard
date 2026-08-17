import { useEffect, useState } from 'react';
import { RunsSidebar } from './components/RunsSidebar';
import { RunDetailsModal } from './components/RunDetailsModal';
import { WorkspaceGrid } from './components/WorkspaceGrid';
import { localTrackStore } from './store';

export function LocalTrackWorkspacePane() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  useEffect(() => {
    localTrackStore.init();
  }, []);

  return (
    <div
      style={{
        display: 'flex',
        width: '100%',
        height: '100%',
        overflow: 'hidden',
        background: 'var(--bg-primary, #0d1117)',
        color: 'var(--text-primary, #c9d1d9)',
        fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      }}
    >
      {/* Left Runs Sidebar */}
      <RunsSidebar
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
      />

      {/* Main Draggable Workspace Grid */}
      <WorkspaceGrid />

      {/* Run Inspection Modal */}
      <RunDetailsModal />
    </div>
  );
}
