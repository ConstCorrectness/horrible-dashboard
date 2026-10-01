/**
 * The inspect choreographies, sampled.
 *
 * One clip per weapon and knife archetype, authored in
 * `tools/blender/author_inspects.py` (which documents what every channel means)
 * and read from `models/inspects.json` by this file and by the native client's
 * `inspects.rs`. The script's own reference sampler writes
 * `models/inspects.golden.json`, and both clients' tests hold their samplers to
 * it — three samplers, one set of numbers.
 *
 * Pure: no three, no clock. `viewmodel.ts` decides what the numbers move.
 */
import inspectsData from './models/inspects.json';

export type Vec3 = [number, number, number];
export type Curl = [number, number, number, number, number];

export interface InspectPose {
  primary?: Vec3;
  support?: Vec3;
  primaryFingers?: Curl;
  supportFingers?: Curl;
}

export interface InspectKey {
  t: number;
  pos: Vec3;
  rot: Vec3;
  spin?: number;
  pose?: InspectPose;
  nodes?: Record<string, { rot: Vec3 }>;
}

export interface InspectClip {
  duration: number;
  /** Where a repeated press jumps back to, as a fraction of the clip. */
  repeatFrom?: number;
  /** The prop node `spin` turns about; the prop's own origin when absent. */
  spinPivot?: string;
  /** The axis `spin` turns about, in weapon space. */
  spinAxis?: Vec3;
  keys: InspectKey[];
}

/**
 * A finger channel and how much of it to apply over the grip's own curl.
 *
 * A weight rather than a value alone, because a key with no fingers means "the
 * grip's curl" and only the caller knows what that is: sampled as a hard value
 * the fingers snapped open on the first frame of every inspect.
 */
export interface FingerSample {
  curl: Curl;
  weight: number;
}

export interface InspectSample {
  /** The whole weapon, on the pivot. Faded to exactly zero at both ends. */
  pos: Vec3;
  rot: Vec3;
  /** The weapon alone about `spinAxis` through `spinPivot`. Not faded. */
  spin: number;
  /** Hand offsets from the grip; absent channels are zero. */
  primary: Vec3;
  support: Vec3;
  primaryFingers: FingerSample | null;
  supportFingers: FingerSample | null;
  nodes: Record<string, Vec3>;
}

export const INSPECT_CLIPS = inspectsData as unknown as Record<string, InspectClip>;

/** The clip a weapon with no clip of its own falls back to. */
const FALLBACK = 'assault';

export function catmullRom(p0: number, p1: number, p2: number, p3: number, u: number): number {
  return (
    0.5 *
    (2 * p1 +
      (-p0 + p2) * u +
      (2 * p0 - 5 * p1 + 4 * p2 - p3) * u * u +
      (-p0 + 3 * p1 - 3 * p2 + p3) * u * u * u)
  );
}

export function smootherstep(u: number): number {
  const x = Math.max(0, Math.min(1, u));
  return x * x * x * (x * (x * 6 - 15) + 10);
}

function round5(v: number): number {
  return Math.round(v * 1e5) / 1e5;
}

const ZERO: Vec3 = [0, 0, 0];

function lerp3(a: Vec3, b: Vec3, u: number): Vec3 {
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u];
}

function fingers(a: Curl | undefined, b: Curl | undefined, u: number): FingerSample | null {
  if (!a && !b) return null;
  if (a && b) {
    return { curl: a.map((v, i) => v + (b[i] - v) * u) as Curl, weight: 1 };
  }
  // One side has none: hold the side that does and fade its weight, so the
  // hand eases between the grip's curl and the key's rather than jumping.
  return a ? { curl: [...a] as Curl, weight: 1 - u } : { curl: [...b!] as Curl, weight: u };
}

/** Sample a clip at `tNorm`, a fraction of its duration. */
export function sampleInspect(clipId: string, tNorm: number): InspectSample {
  const clip = INSPECT_CLIPS[clipId] ?? INSPECT_CLIPS[FALLBACK];
  const keys = clip.keys;
  const t = Math.max(0, Math.min(1, tNorm));

  let idx = 0;
  while (idx < keys.length - 2 && keys[idx + 1].t < t) idx += 1;
  const k1 = keys[idx];
  const k2 = keys[idx + 1];
  const k0 = keys[Math.max(0, idx - 1)];
  const k3 = keys[Math.min(keys.length - 1, idx + 2)];
  const span = k2.t - k1.t;
  const u = span <= 1e-9 ? 0 : Math.max(0, Math.min(1, (t - k1.t) / span));

  let fade = 1;
  if (t < 0.05) fade = smootherstep(t / 0.05);
  else if (t > 0.95) fade = smootherstep((1 - t) / 0.05);

  const curve = (pick: (k: InspectKey) => number) =>
    catmullRom(pick(k0), pick(k1), pick(k2), pick(k3), u);
  const pos = [0, 1, 2].map((i) => round5(curve((k) => k.pos[i]) * fade)) as Vec3;
  const rot = [0, 1, 2].map((i) => round5(curve((k) => k.rot[i]) * fade)) as Vec3;
  const spin = round5(curve((k) => k.spin ?? 0));

  const a = k1.pose ?? {};
  const b = k2.pose ?? {};
  const nodes: Record<string, Vec3> = {};
  const names = new Set([...Object.keys(k1.nodes ?? {}), ...Object.keys(k2.nodes ?? {})]);
  for (const name of names) {
    const rotOf = (k: InspectKey) => k.nodes?.[name]?.rot ?? ZERO;
    nodes[name] = [0, 1, 2].map((i) =>
      catmullRom(rotOf(k0)[i], rotOf(k1)[i], rotOf(k2)[i], rotOf(k3)[i], u),
    ) as Vec3;
  }

  return {
    pos,
    rot,
    spin,
    primary: lerp3(a.primary ?? ZERO, b.primary ?? ZERO, u),
    support: lerp3(a.support ?? ZERO, b.support ?? ZERO, u),
    primaryFingers: fingers(a.primaryFingers, b.primaryFingers, u),
    supportFingers: fingers(a.supportFingers, b.supportFingers, u),
    nodes,
  };
}

/** Blend a sampled finger channel over the grip's own curl. */
export function blendCurl(grip: Curl, sample: FingerSample | null): Curl {
  if (!sample) return grip;
  return grip.map((v, i) => v + (sample.curl[i] - v) * sample.weight) as Curl;
}

/**
 * Which knife this skin is, from its id **and** its name.
 *
 * Both, because the prop is picked from both: a skin whose id is a catalogue
 * number and whose name says "Karambit" is drawn as a karambit, and inspected
 * as one.
 */
export function knifeArchetype(skinId: string, skinName = ''): string {
  const s = `${skinId} ${skinName}`.toLowerCase();
  if (s.includes('karambit')) return 'knife-karambit';
  if (s.includes('butterfly')) return 'knife-butterfly';
  if (s.includes('bayonet') || s.includes('lore')) return 'knife-bayonet';
  if (s.includes('skeleton')) return 'knife-skeleton';
  if (s.includes('huntsman')) return 'knife-huntsman';
  return 'knife-tactical';
}

/** The prop id for a knife archetype, as `models/weapons.ts` keys them. */
export function knifePropId(archetype: string): string {
  return archetype === 'knife-tactical' ? 'knife' : archetype.replace('-', '_');
}

export function inspectClipFor(weaponId: string, skinId = '', skinName = ''): string {
  if (weaponId === 'knife') return knifeArchetype(skinId, skinName);
  if (weaponId.startsWith('nade') || weaponId.startsWith('grenade')) return 'nade';
  return weaponId in INSPECT_CLIPS ? weaponId : FALLBACK;
}

export function clipFor(clipId: string): InspectClip {
  return INSPECT_CLIPS[clipId] ?? INSPECT_CLIPS[FALLBACK];
}
