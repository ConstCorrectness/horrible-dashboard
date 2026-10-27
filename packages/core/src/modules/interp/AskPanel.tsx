/**
 * Ask: the interpretability agent, in a strip beside whatever tool is open.
 *
 * A region rather than a section, because a section *replaces* the tool you are
 * asking about. The chat is the ordinary `ChatWidget` pinned to the `interp`
 * roster agent — its own sessions, no agent picker, and it takes only the "ask
 * about this" sends addressed to it (`ask.ts`). The transcript lives in
 * `chat-state.ts`, so closing the strip or switching tools loses nothing.
 */
import { ChatWidget } from '../agent/ChatWidget';
import { INTERP_AGENT } from './ask';

export function AskPanel() {
  return <ChatWidget agentId={INTERP_AGENT} compact />;
}
