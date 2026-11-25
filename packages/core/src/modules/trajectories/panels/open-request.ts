/**
 * Deep links into the Wire Requests sub-tab of Runs: pin a turn there and open
 * one of its provider calls. Kept apart from `request-model.ts` so that file
 * stays free of the layout controller and testable without a DOM.
 */
import { focusFollowTarget, type FollowFocus } from '../../agent/chat-state';
import { SUITE_SECTIONS } from '../../interp/ids';
import { openSuiteTab } from '../../interp/stage';

export function openRequest(
  agentId: string | null | undefined,
  turnId: string,
  focus: Omit<FollowFocus, 'nonce'>,
): void {
  focusFollowTarget(agentId || 'main', turnId, focus);
  openSuiteTab(SUITE_SECTIONS.runs, { subTab: 'follow' });
}
