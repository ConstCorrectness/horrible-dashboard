/**
 * The Agent button: the orchestrator, one click away from anywhere.
 *
 * `agent.chat` is a `role: 'tool'` widget that lives in a dock, and a dock can be
 * hidden; without this button the one pane you reach for constantly would be
 * available only through the palette or the Start menu. This is its permanent
 * home, at the far end of the strip.
 *
 * It **toggles** rather than reopening: an already-open chat is routed through
 * `activateTaskbarEntry`, the exact verb a pane button runs, so the second
 * click hides its dock and the third brings it back. Calling `openPanel` every time
 * would look like nothing happened once the pane was already up.
 */
import { useSyncExternalStore } from 'react';
import { activateTaskbarEntry, layoutStore, registry, taskbarEntries } from '@horrible/core';

const AGENT_VIEW = 'agent.chat';

export function AgentButton({ showLabels }: { showLabels: boolean }) {
  const { frame } = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  // The first instance, not "any": the chat is a singleton in practice, and if a
  // second ever exists the oldest is the one the user means by "the agent".
  const entry = taskbarEntries(frame).find((e) => e.viewId === AGENT_VIEW);
  return (
    <button
      type="button"
      className={`os-taskbar-agent${entry && entry.state !== 'minimized' ? ' is-live' : ''}`}
      aria-label="Agent"
      aria-pressed={entry?.state === 'focused'}
      title="Agent"
      onClick={() => {
        // Not open yet: `openPane` places it (its dock, shown) and focuses it.
        if (entry) activateTaskbarEntry(entry.instanceId);
        else registry.openPanel(AGENT_VIEW);
      }}
    >
      <span className="os-taskbar-agent-icon" aria-hidden="true">
        🤖
      </span>
      {showLabels && <span>Agent</span>}
    </button>
  );
}
