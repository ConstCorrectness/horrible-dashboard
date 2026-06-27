/**
 * The flow library pane: list saved flows, create new ones, and open one into a
 * flow editor pane (passing its id as the `flowId` param so reopening focuses the
 * same instance).
 */
import { useCallback, useEffect, useState } from 'react';

import { registry } from '../../../registry';
import { createFlow, deleteFlow, getFlows, type Flow } from '../flows';

/** Open (or focus) the editor for a flow. */
export function openFlow(id: string): void {
  registry.openPanel('flow.editor', {
    instanceId: `flow.editor:${id}`,
    params: { flowId: id },
  });
}

export function FlowLibraryPanel() {
  const [flows, setFlows] = useState<Flow[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    getFlows()
      .then((s) => setFlows(s.flows))
      .catch(() => setFlows([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(refresh, [refresh]);

  const onNew = useCallback(async () => {
    const flow = await createFlow(`Flow ${flows.length + 1}`);
    refresh();
    openFlow(flow.id);
  }, [flows.length, refresh]);

  const onDelete = useCallback(
    async (id: string) => {
      await deleteFlow(id);
      refresh();
    },
    [refresh],
  );

  return (
    <div className="flow-library">
      <button className="flow-btn flow-btn-new" onClick={onNew}>
        ＋ New flow
      </button>
      {loading ? (
        <div className="flow-empty">Loading…</div>
      ) : flows.length === 0 ? (
        <div className="flow-empty">No flows yet. Create one to start orchestrating.</div>
      ) : (
        <ul className="flow-library-list">
          {flows.map((f) => (
            <li key={f.id}>
              <button className="flow-library-open" onClick={() => openFlow(f.id)}>
                {f.name}
                <span className="flow-library-meta">{f.nodes.length} nodes</span>
              </button>
              <button
                className="flow-library-del"
                title="Delete flow"
                onClick={() => onDelete(f.id)}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
