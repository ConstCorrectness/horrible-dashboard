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
      <AgentGlyph />
      {showLabels && <span>Agent</span>}
    </button>
  );
}

/**
 * A robot head in strokes on `currentColor`, so it takes the button's text colour
 * and its hover state. It replaced a 🤖 emoji, which drew in the OS's colour font
 * at whatever size that font picked and ignored the theme entirely.
 */
function AgentGlyph() {
  return (
    <svg
      className="os-taskbar-agent-icon"
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M7 1.6v2" />
      <rect x="2.2" y="3.6" width="9.6" height="7.4" rx="2" />
      <path d="M5.6 9h2.8M1 6.6v2M13 6.6v2" />
      <circle cx="5.1" cy="6.6" r="0.75" fill="currentColor" stroke="none" />
      <circle cx="8.9" cy="6.6" r="0.75" fill="currentColor" stroke="none" />
    </svg>
  );
}
