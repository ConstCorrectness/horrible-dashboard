import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/**
 * The pane returns from two places — the room view (`if (joined && activeChannel)`)
 * and the lobby below it. The network inspector was mounted only in the lobby's
 * tree, while *both* buttons that open it live in the room view, so in a room the
 * click set the flag, the button lit up as active, and nothing rendered.
 *
 * Asserted against the source rather than a render: this pane is ~5000 lines behind
 * a live Clubhouse session, an Agora client and a PubNub socket, so mounting it in
 * jsdom would test the mocks. What broke here was purely *where the element sits
 * between two returns*, and that is visible in the file.
 *
 * Every needle below is newline-free on purpose — the working tree is CRLF on
 * Windows, and a pattern written with a bare LF silently matches nothing.
 */
const SRC = readFileSync(fileURLToPath(new URL('../RoomsPanel.tsx', import.meta.url)), 'utf8');

describe('the network inspector', () => {
  it('is mounted in both the room view and the lobby', () => {
    const roomBranch = SRC.indexOf('if (joined && activeChannel) {');
    const lobbyView = SRC.indexOf('<div className="ch-rooms">');
    expect(roomBranch).toBeGreaterThan(-1);
    expect(lobbyView).toBeGreaterThan(roomBranch);

    const mounts = [...SRC.matchAll(/\{networkModal\}/g)].map((m) => m.index!);
    expect(mounts).toHaveLength(2);
    // One inside the room-view branch, one after it in the lobby.
    expect(mounts[0]).toBeGreaterThan(roomBranch);
    expect(mounts[0]).toBeLessThan(lobbyView);
    expect(mounts[1]).toBeGreaterThan(lobbyView);
  });

  it('is built once, so the two mounts cannot drift', () => {
    expect([...SRC.matchAll(/<MediaInsightsModal/g)]).toHaveLength(1);
  });

  it('opens from buttons that toggle rather than latch', () => {
    // Three openers: the room header's pill, the Agent panel's Inspect row, and
    // the lobby header — the lobby one matters most, because it is where you end up
    // when a join is what failed.
    const togglers = [...SRC.matchAll(/setShowNetworkModal\(\(open\) => !open\)/g)];
    expect(togglers).toHaveLength(3);
    const lobbyView = SRC.indexOf('<div className="ch-rooms">');
    expect(togglers.some((m) => m.index! > lobbyView)).toBe(true);
    // A bare `setShowNetworkModal(true)` is the latch this replaced: it leaves a
    // button rendering an active state that a second click cannot turn off.
    expect(SRC).not.toMatch(/setShowNetworkModal\(true\)/);
  });

  it('keeps the lobby opener out of the ready gate', () => {
    /*
     * Caught live, not reasoned about: with `state === 'ready'` around it, the
     * button vanished the moment Clubhouse's edge returned its intermittent 503 on
     * the room feed — the lobby rendered the error with only Refresh beside it, and
     * the inspector was unreachable in the one state it exists to explain.
     *
     * Asserted by looking at what precedes the opener: the sibling controls each sit
     * behind their own `{state === 'ready' && (`, and this one must not.
     */
    const lobbyView = SRC.indexOf('<div className="ch-rooms">');
    const opener = SRC.indexOf('📡 Network', lobbyView);
    expect(opener).toBeGreaterThan(-1);

    // Everything between the doc comment that introduces this button and the
    // button's own tag. A gate wrapping it can only live in that gap, so this is
    // the span to look at — a fixed-size window before the tag is not, because the
    // gate sits on the far side of the comment and slips out of it.
    const tag = SRC.lastIndexOf('<button', opener);
    const commentEnd = SRC.lastIndexOf('*/}', tag);
    expect(commentEnd).toBeGreaterThan(-1);
    expect(SRC.slice(commentEnd, tag)).not.toContain('state ===');
  });

  it("keeps the Agent panel's Inspect row out of the agent-enabled gate", () => {
    /*
     * Same shape of bug, found the same way — in the pane, in a live room. The
     * telemetry row sat deep inside `{agentEnabled && (…)}`, so with the voice agent
     * paused (the state the panel opens in) the button was not on the page at all.
     * Nothing about UDP, PubNub or Agora depends on the agent running.
     *
     * Asserted by ordering: the row is hoisted above the `agentEnabled` block, so
     * its index must precede that block's.
     */
    const inspect = SRC.indexOf('📡 Protocols & Media Telemetry');
    expect(inspect).toBeGreaterThan(-1);

    // Anchored to the start of a line, because the comment above the hoisted row
    // *quotes* `{agentEnabled && (` — an unanchored indexOf matches that prose and
    // compares the row against a sentence about the row.
    const blocks = [...SRC.matchAll(/^[ \t]*\{agentEnabled && \(/gm)].map((m) => m.index!);
    const gate = blocks.find((i) => i > SRC.search(/^[ \t]*\{!agentEnabled && \(/m));
    expect(gate).toBeDefined();
    expect(inspect).toBeLessThan(gate!);
  });
});
