/**
 * Frontend half of the permission approval round-trip. The backend gate
 * (`_gate` in orchestrator.py) sends an `approval_request` on the `agent` channel
 * when a side-effecting tool call needs the user's decision; this store collects
 * those prompts so a global UI can render them, and `respondApproval` sends the
 * `approval_response` back. See docs/architecture/agent-tools.md.
 */
import { useSyncExternalStore } from 'react';

import { hasCapability } from '../../capabilities';
import { sendChannel, subscribeChannel } from '../../ws';

export type ApprovalDecision = 'allow_once' | 'allow_always' | 'deny';

export interface PendingApproval {
  approvalId: string;
  tool: string;
  /** The rendered permission specifier (e.g. the shell command), or null. */
  specifier: string | null;
  mode: string;
}

let pending: PendingApproval[] = [];
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function notifyDesktop(a: PendingApproval): void {
  // Best-effort OS notification on platforms that have it (desktop). The browser
  // shows the in-app surface only.
  if (!hasCapability('notifications.system')) return;
  try {
    if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      new Notification('Agent needs approval', {
        body: a.specifier ? `${a.tool}: ${a.specifier}` : a.tool,
      });
    }
  } catch {
    // Notifications unavailable — the in-app prompt still shows.
  }
}

let started = false;

/** Begin listening for approval prompts on the agent channel. Idempotent. */
export function initApprovalListener(): void {
  if (started) return;
  started = true;
  subscribeChannel('agent', (msg) => {
    if (msg.event !== 'approval_request') return;
    const d = (msg.data ?? {}) as Record<string, unknown>;
    const approvalId = String(d.approvalId ?? '');
    if (!approvalId || pending.some((p) => p.approvalId === approvalId)) return;
    const item: PendingApproval = {
      approvalId,
      tool: String(d.tool ?? ''),
      specifier: d.specifier == null ? null : String(d.specifier),
      mode: String(d.mode ?? ''),
    };
    pending = [...pending, item];
    emit();
    notifyDesktop(item);
  });
}

/** Answer a pending approval and remove it from the queue. */
export function respondApproval(
  approvalId: string,
  decision: ApprovalDecision,
  rule?: string,
): void {
  sendChannel('agent', 'approval_response', {
    approvalId,
    decision,
    ...(rule ? { rule } : {}),
  });
  pending = pending.filter((p) => p.approvalId !== approvalId);
  emit();
}

const approvalsStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  getSnapshot(): PendingApproval[] {
    return pending;
  },
};

/** Reactive list of approval prompts awaiting a decision. */
export function useApprovals(): PendingApproval[] {
  return useSyncExternalStore(approvalsStore.subscribe, approvalsStore.getSnapshot);
}
