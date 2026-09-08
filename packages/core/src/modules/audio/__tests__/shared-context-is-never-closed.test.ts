/**
 * Nobody may close the shared AudioContext.
 *
 * `AudioMixer.getContext()` creates one context on first use and caches it for the
 * life of the page — it never notices, nor rebuilds, a closed one. So a module that
 * treats `ctx.close()` as its own cleanup does not tear down its audio, it silences
 * the entire app: karaoke, the agent's voice, hassault, and every later join, whose
 * first `createGain()` / `createMediaStreamDestination()` throws `InvalidStateError`
 * on a closed context. Nothing reports any of that.
 *
 * The regression: Clubhouse's room teardown closed the mixer's context, so leaving
 * one room left the microphone dead in the next one while the button still read
 * "Mic Active". hassault had the same line and was fixed; Clubhouse was missed —
 * which is why this is a rule enforced across the tree rather than a comment in two
 * files. Release a strip or disconnect your own nodes instead.
 *
 * A closed context is invisible at runtime and unreachable from a unit test without
 * standing up Web Audio, so this reads the source — the `control-padding.test.ts`
 * approach.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const srcRoot = fileURLToPath(new URL('../../..', import.meta.url));

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '__tests__') continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...sourceFiles(full));
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

/** `ctx.close()`, `audioCtx.close()`, `this.audioContext.close()`, … */
const CLOSE_CALL = /\b(?:this\.)?[\w$]*(?:[Cc]ontext|[Cc]tx)\??\.close\s*\(/;

describe('the shared AudioContext is never closed', () => {
  it('has no module calling close() on an audio context', () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(srcRoot)) {
      const lines = readFileSync(file, 'utf8').split('\n');
      lines.forEach((line, i) => {
        if (line.trimStart().startsWith('*') || line.trimStart().startsWith('//')) return;
        if (CLOSE_CALL.test(line)) {
          offenders.push(`${file.slice(srcRoot.length)}:${i + 1}: ${line.trim()}`);
        }
      });
    }
    expect(offenders, offenders.join('\n')).toEqual([]);
  });
});
