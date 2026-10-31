/**
 * A plan-mode turn's plan (`submit_plan`), rendered as markdown with the two ways
 * on from it: approve, which switches the chat to Agent and has it carry the plan
 * out, or keep planning, which hands the composer back.
 */
import { renderMarkdown } from '../../notebook/markdown';
import type { TurnPlan } from './turn-steps';

const STATUS_TEXT: Record<TurnPlan['status'], string> = {
  proposed: 'awaiting approval',
  approved: 'approved',
  superseded: 'superseded',
};

export function PlanCard({
  plan,
  canAct,
  onApprove,
  onKeepPlanning,
}: {
  plan: TurnPlan;
  /** False while another turn runs: approving would start a second one. */
  canAct: boolean;
  onApprove: () => void;
  onKeepPlanning: () => void;
}) {
  return (
    <section className="agent-card agent-plan" data-status={plan.status} aria-label="Plan">
      <header className="agent-card-head">
        <span className="agent-card-title">Plan</span>
        <span className="agent-plan-status">{STATUS_TEXT[plan.status]}</span>
      </header>
      <div
        className="agent-markdown agent-plan-body"
        // renderMarkdown escapes its input before adding markup (notebook/markdown.ts).
        dangerouslySetInnerHTML={{ __html: renderMarkdown(plan.text) }}
      />
      {plan.status === 'proposed' && (
        <div className="agent-card-actions">
          <button type="button" disabled={!canAct} onClick={onApprove}>
            Approve &amp; run
          </button>
          <button type="button" disabled={!canAct} onClick={onKeepPlanning}>
            Keep planning
          </button>
        </div>
      )}
    </section>
  );
}
