/**
 * Deep links into the Agent section: pin a turn there and open one of its provider
 * calls. Kept apart from `request-model.ts` so that file stays free of the layout
 * controller and testable without a DOM.
 */
import { revealSection } from '../../../layout/controller';
import { focusFollowTarget, type FollowFocus } from '../../agent/chat-state';

export function openRequest(
  agentId: string | null | undefined,
  turnId: string,
  focus: Omit<FollowFocus, 'nonce'>,
): void {
  focusFollowTarget(agentId || 'main', turnId, focus);
  revealSection('follow', 'trajectories.hub');
}
