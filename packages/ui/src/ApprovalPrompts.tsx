/**
 * Global surface for agent permission prompts. When the backend gate needs the
 * user's decision it sends `approval_request`; these render as cards with
 * Allow once / Always allow / Deny. "Always allow" sends a rule (editable, so the
 * user can broaden it, e.g. `terminal.exec(npm run *)`) that the backend persists.
 * See docs/architecture/agent-tools.md.
 */
import { useState } from 'react';
import { respondApproval, useApprovals, type PendingApproval } from '@horrible/core';

function ApprovalCard({ a }: { a: PendingApproval }) {
  const display = a.specifier ? `${a.tool}(${a.specifier})` : a.tool;
  const [rule, setRule] = useState(display);
  return (
    <div className="approval-card">
      <div className="approval-head">Agent wants to run</div>
      <code className="approval-tool">{display}</code>
      <div className="approval-mode">mode: {a.mode}</div>
      <label className="approval-rule">
        Always-allow rule
        <input value={rule} onChange={(e) => setRule(e.target.value)} spellCheck={false} />
      </label>
      <div className="approval-actions">
        <button onClick={() => respondApproval(a.approvalId, 'allow_once')}>Allow once</button>
        <button
          onClick={() => respondApproval(a.approvalId, 'allow_always', rule.trim() || undefined)}
        >
          Always allow
        </button>
        <button className="approval-deny" onClick={() => respondApproval(a.approvalId, 'deny')}>
          Deny
        </button>
      </div>
    </div>
  );
}

export function ApprovalPrompts() {
  const pending = useApprovals();
  if (pending.length === 0) return null;
  return (
    <div className="approval-overlay">
      {pending.map((a) => (
        <ApprovalCard key={a.approvalId} a={a} />
      ))}
    </div>
  );
}
