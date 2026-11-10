/**
 * Which agent turn is calling the tool that is running now, and how to stop it.
 *
 * The relay (modules/agent/orchestrator-client.ts) knows each relayed call's turn;
 * a tool handler does not, and its signature (`args`) has no room for it. So the
 * relay runs the handler inside {@link runInTurn}, and the handler reads
 * {@link relayTurn} **before its first `await`**. That is the same trick React
 * uses for its current dispatcher, and it holds for the same reason: the relay's
 * path down to the handler (`executeTool` → `executeDynamicTool` → `handler`) is
 * synchronous up to the handler's own first `await`, so nothing else can run in
 * between.
 *
 * The browser uses it to mark the page a turn is driving, and to give the person
 * a Stop for exactly that turn (browser/driving.ts).
 */
import { sendChannel } from './ws';

let current: string | null = null;

/** Run `fn` with `turnId` as the calling turn for its synchronous start. */
export function runInTurn<T>(turnId: string | null, fn: () => T): T {
  const previous = current;
  current = turnId;
  try {
    return fn();
  } finally {
    current = previous;
  }
}

/**
 * The turn whose relayed call is starting, or null outside one (a person pressed a
 * button). Only meaningful before the caller's first `await`.
 */
export function relayTurn(): string | null {
  return current;
}

/** Ask the backend to stop `turnId`: the chat's own Stop sends the same. */
export function cancelTurn(turnId: string): void {
  sendChannel('agent', 'cancel', { turnId });
}
