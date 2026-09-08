/**
 * Switching rooms must not carry the previous room into the new one.
 *
 * `joinRoom` resets the *session* (`joined`, `comments`, `liveUsers`, …), but
 * `RoomsPanel` holds its own per-room React state that nothing reset. The reported
 * symptom was the LIVE STT panel: it kept showing the last thing said in the room
 * you left instead of returning to "Microphone listening for room audio…", which is
 * the only evidence in the pane that the agent's ears work — so a stale line reads
 * as a live transcript, and a genuinely fresh room reads as one nobody has spoken
 * in yet.
 *
 * Two of the entries below are worse than cosmetic and are the reason this is
 * pinned rather than left to review: a queued utterance is addressed at *drain*
 * time from `activeChannelRef.current`, so anything still queued across a switch is
 * answered into the new room; and `agentSentTextsRef` is keyed on text alone, so an
 * uncleared set suppresses a genuine line in the new room because the old one said
 * it.
 *
 * An effect's dependency list is not reachable from a unit test without rendering
 * `RoomsPanel`, which pulls in the whole module surface, so this reads the source —
 * the `joinOrder.test.ts` / `control-padding.test.ts` approach.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const source = readFileSync(fileURLToPath(new URL('../RoomsPanel.tsx', import.meta.url)), 'utf8');

/** The reset effect: the one `useEffect` whose dependency list is the active channel. */
const resetEffect = (() => {
  const start = source.indexOf('setLastHeardSpeech(null)');
  if (start === -1) return '';
  const open = source.lastIndexOf('useEffect(() => {', start);
  const close = source.indexOf('}, [activeChannel', start);
  if (open === -1 || close === -1) return '';
  return source.slice(open, source.indexOf('\n', close));
})();

describe('a room switch clears the pane state that described the old room', () => {
  it('has an effect keyed on the active channel', () => {
    expect(resetEffect, 'no reset effect keyed on activeChannel').not.toBe('');
    expect(resetEffect).toMatch(/\}, \[activeChannel[,\]]/);
  });

  it.each([
    // The reported bug: the placeholder must come back.
    ['setLastHeardSpeech(null)', 'the live STT line'],
    ['setAgentReason(null)', "the previous room's reason for staying quiet"],
    ['setAgentMemory([])', "the previous room's remembered conversation"],
    ['setCommentText', 'a chat draft typed in the room you left'],
    ['agentQueueRef.current = []', 'utterances that would be answered into the new room'],
    ['agentSentTextsRef.current.clear()', 'the echo-dedup set, which is keyed on text alone'],
    ['agentAbortControllerRef.current.abort()', 'a generation in flight for the old room'],
    ['stopAgentAudio()', 'a reply still being spoken into the room you left'],
  ])('clears %s — %s', (fragment) => {
    expect(resetEffect).toContain(fragment);
  });
});
