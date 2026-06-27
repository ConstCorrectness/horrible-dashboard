/**
 * The flow editor pane: hosts the canvas for one flow. Multi-instance — opened with
 * a `flowId` param (from the library or a command). With no param it falls back to
 * the active flow, creating a first one so the Orchestration layout is usable out of
 * the box. Wraps the canvas in ReactFlowProvider (required for the engine hooks).
 */
import { ReactFlowProvider } from '@xyflow/react';
import { useEffect, useState } from 'react';

import { usePaneParams } from '../../../panes';
import { FlowCanvas } from '../canvas/FlowCanvas';
import { createFlow, getFlows } from '../flows';

export function FlowEditorPanel() {
  const params = usePaneParams();
  const paramFlowId = typeof params.flowId === 'string' ? params.flowId : null;
  const [flowId, setFlowId] = useState<string | null>(paramFlowId);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (paramFlowId) {
      setFlowId(paramFlowId);
      return;
    }
    let cancelled = false;
    // No explicit flow: open the active one, or create a first flow.
    getFlows()
      .then(async (state) => {
        if (cancelled) return;
        const id = state.active ?? (await createFlow('My first flow')).id;
        if (!cancelled) setFlowId(id);
      })
      .catch(() => !cancelled && setError('Backend unavailable — cannot load flows.'));
    return () => {
      cancelled = true;
    };
  }, [paramFlowId]);

  if (error) return <div className="flow-empty">{error}</div>;
  if (!flowId) return <div className="flow-empty">Loading flow…</div>;
  return (
    <ReactFlowProvider>
      <FlowCanvas flowId={flowId} />
    </ReactFlowProvider>
  );
}
