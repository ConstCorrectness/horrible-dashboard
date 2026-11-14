/**
 * Speaking levels as the room pane shows them.
 *
 * Agora's volume indicator fires every 200 ms with a 0–100 level per uid, and each
 * level that reaches room state re-renders the whole room pane. The pane only uses
 * a level two ways: "is this person talking" (`> 8`) and the size of their ring
 * (`level / 60`, capped at 1). So levels are bucketed to what those two can show,
 * and an update that changes no bucket changes nothing.
 */

/** At or below this a uid is not speaking at all and is dropped. */
const SILENT = 5;
/** Ring size saturates here (`RoomsPanel` divides by 60 and caps at 1). */
const RING_MAX = 60;
/** Bucket width: 9 → 12 stays above the speaking threshold of 8, 6–8 → 6 below it. */
const STEP = 6;

/** The levels worth rendering: quiet uids dropped, the rest bucketed and capped. */
export function displayVolumes(raw: Record<number, number>): Record<number, number> {
  const shown: Record<number, number> = {};
  for (const [uid, level] of Object.entries(raw)) {
    if (!(level > SILENT)) continue;
    shown[Number(uid)] = Math.min(RING_MAX, Math.max(STEP, Math.round(level / STEP) * STEP));
  }
  return shown;
}

/** Same uids at the same levels. */
export function sameVolumes(a: Record<number, number>, b: Record<number, number>): boolean {
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  for (const k of keys) {
    if (a[Number(k)] !== b[Number(k)]) return false;
  }
  return true;
}
