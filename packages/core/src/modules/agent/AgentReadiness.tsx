/**
 * The agent pane's readiness banner.
 *
 * `AgentStatus` carries two independent booleans — `configured` (has the user
 * picked a provider and model?) and `reachable` (is that provider answering right
 * now?) — plus the two failure modes that live outside the payload entirely:
 * the backend not answering at all, and the status not having resolved yet.
 *
 * Those are four different problems with four different remedies, and the pane
 * used to render every one of them as the same grey input placeholder
 * ("Agent not ready"). That is the inversion of this product's own rule: a
 * capability check reports what it found, what it looked for and failed to find,
 * and what it could not ask — never one collapsed "no".
 *
 * So each state names its cause and carries the control that fixes it. The
 * endpoint is telemetry and takes the mono treatment; the heading is identity and
 * takes the tracked-uppercase one.
 */
import { IconAlert } from '../../glyphs';
import { registry } from '../../registry';
import { Button, Chip } from '../../Primitives';
import type { AgentStatus } from './api';

export type ReadinessState = AgentStatus | 'loading' | 'backend-down';

interface Plan {
  kind: 'warn' | 'fail' | 'info';
  /** Identity line — what is wrong, in the user's terms. */
  title: string;
  /** One sentence of cause, or what to do. Never both a hint and an error. */
  body: string;
  /** Telemetry: the endpoint we actually tried, when there was one. */
  endpoint?: string;
  actions: Array<{ label: string; intent?: 'primary' | 'default'; run: () => void }>;
}

/**
 * Resolve a status into the one thing to say about it.
 *
 * Exported for its own test: this is the branch that used to be a single `&&`,
 * and the states it distinguishes are the whole point of the component.
 */
export function planFor(status: ReadinessState, onRetry: () => void): Plan | null {
  const openAgentSettings = () => registry.openPanel('settings.home');

  if (status === 'loading') {
    return {
      kind: 'info',
      title: 'Checking the agent',
      body: 'Asking the node which model is configured and whether it is answering.',
      actions: [],
    };
  }

  if (status === 'backend-down') {
    return {
      kind: 'fail',
      title: 'Backend not answering',
      body: 'The interface is running, but this node’s backend is not responding. Start it, then retry.',
      actions: [{ label: 'Retry', intent: 'primary', run: onRetry }],
    };
  }

  if (!status.configured) {
    return {
      kind: 'warn',
      title: 'No model selected',
      body: 'Choose a provider and model for the agent to think with. Nothing is sent anywhere else.',
      actions: [{ label: 'Choose a model', intent: 'primary', run: openAgentSettings }],
    };
  }

  if (!status.reachable) {
    return {
      kind: 'warn',
      title: `Can’t reach ${status.provider ?? 'the provider'}`,
      body: 'The model is configured but its server is not answering. Start it, or point the agent somewhere else.',
      endpoint: status.endpoint,
      actions: [
        { label: 'Retry', intent: 'primary', run: onRetry },
        { label: 'Change provider', run: openAgentSettings },
      ],
    };
  }

  return null;
}

export function AgentReadiness({
  status,
  onRetry,
}: {
  status: ReadinessState;
  onRetry: () => void;
}) {
  const plan = planFor(status, onRetry);
  if (!plan) return null;

  return (
    <div className="agent-readiness" data-kind={plan.kind} role="status">
      <div className="agent-readiness-head">
        <IconAlert />
        <span className="agent-readiness-title">{plan.title}</span>
        {plan.endpoint && (
          <Chip kind={plan.kind} title="The endpoint the agent tried">
            {plan.endpoint}
          </Chip>
        )}
      </div>
      <p className="agent-readiness-body">{plan.body}</p>
      {plan.actions.length > 0 && (
        <div className="agent-readiness-actions">
          {plan.actions.map((a) => (
            <Button key={a.label} intent={a.intent ?? 'default'} size="sm" onClick={a.run}>
              {a.label}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
