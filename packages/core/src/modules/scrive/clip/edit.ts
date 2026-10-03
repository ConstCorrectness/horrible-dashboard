/**
 * The clip editor's arithmetic, kept pure so core vitest can pin it: mapping between
 * the source's timeline (where segments live) and the output's (where captions and
 * overlays live), the cut operations, and the crop window — which must match
 * `crop_rect` in backend/modules/scrive/clips.py, or the preview would show a
 * different frame from the render.
 */
import type { ClipCue, ClipSegment, CropAspect, EditList } from '../api';

const ASPECTS: Record<CropAspect, [number, number] | null> = {
  source: null,
  '16:9': [16, 9],
  '9:16': [9, 16],
  '1:1': [1, 1],
  '4:5': [4, 5],
};

/** Shortest segment a cut may leave behind, in source seconds. */
export const MIN_SEGMENT = 0.1;

const len = (s: ClipSegment) => Math.max(0, s.out - s.in);

export function outputDuration(edit: EditList): number {
  return edit.segments.reduce((sum, s) => sum + len(s), 0) / edit.speed;
}

/** Where each segment starts on the output timeline. */
export function segmentStarts(edit: EditList): number[] {
  const starts: number[] = [];
  let at = 0;
  for (const s of edit.segments) {
    starts.push(at);
    at += len(s) / edit.speed;
  }
  return starts;
}

/** The segment holding source time `t` (first match: segments may overlap), or -1. */
export function segmentAt(edit: EditList, t: number): number {
  return edit.segments.findIndex((s) => t >= s.in && t < s.out);
}

/** Source time → output time, or null when `t` is in no segment. */
export function toOutput(edit: EditList, t: number, index = segmentAt(edit, t)): number | null {
  if (index < 0) return null;
  const seg = edit.segments[index];
  return segmentStarts(edit)[index] + (Math.min(t, seg.out) - seg.in) / edit.speed;
}

/** Output time → the segment and source time it shows (clamped to the clip). */
export function toSource(edit: EditList, outT: number): { index: number; t: number } {
  const starts = segmentStarts(edit);
  for (let i = edit.segments.length - 1; i >= 0; i--) {
    if (outT >= starts[i]) {
      const seg = edit.segments[i];
      return { index: i, t: Math.min(seg.out, seg.in + (outT - starts[i]) * edit.speed) };
    }
  }
  return { index: 0, t: edit.segments[0]?.in ?? 0 };
}

function withSegments(edit: EditList, segments: ClipSegment[]): EditList {
  return { ...edit, segments };
}

/** Cut the segment holding source time `t` in two. A cut within MIN_SEGMENT of an
 * end does nothing. */
export function splitAt(edit: EditList, at: number): EditList {
  const t = round3(at);
  const i = segmentAt(edit, t);
  if (i < 0) return edit;
  const seg = edit.segments[i];
  if (t - seg.in < MIN_SEGMENT || seg.out - t < MIN_SEGMENT) return edit;
  const next = [...edit.segments];
  next.splice(i, 1, { in: seg.in, out: t }, { in: t, out: seg.out });
  return withSegments(edit, next);
}

/** Move a segment's in or out point to `t`, keeping it at least MIN_SEGMENT long. */
export function trim(edit: EditList, index: number, end: 'in' | 'out', at: number): EditList {
  const t = round3(at);
  const seg = edit.segments[index];
  if (!seg) return edit;
  const next = [...edit.segments];
  next[index] =
    end === 'in'
      ? { ...seg, in: Math.max(0, Math.min(t, seg.out - MIN_SEGMENT)) }
      : { ...seg, out: Math.max(t, seg.in + MIN_SEGMENT) };
  return withSegments(edit, next);
}

export function removeSegment(edit: EditList, index: number): EditList {
  return withSegments(
    edit,
    edit.segments.filter((_, i) => i !== index),
  );
}

export function moveSegment(edit: EditList, index: number, by: -1 | 1): EditList {
  const to = index + by;
  if (to < 0 || to >= edit.segments.length) return edit;
  const next = [...edit.segments];
  [next[index], next[to]] = [next[to], next[index]];
  return withSegments(edit, next);
}

const even = (v: number) => Math.max(2, Math.floor(v / 2) * 2);

/** The crop window in source pixels — the same numbers `crop_rect` gives ffmpeg. */
export function cropBox(
  crop: EditList['crop'],
  width: number,
  height: number,
): { w: number; h: number; x: number; y: number } {
  const ratio = ASPECTS[crop.aspect];
  if (!ratio) return { w: even(width), h: even(height), x: 0, y: 0 };
  const aspect = ratio[0] / ratio[1];
  let w: number;
  let h: number;
  if (width / height > aspect) {
    h = even(height);
    w = even(height * aspect);
  } else {
    w = even(width);
    h = even(width / aspect);
  }
  // Python's round() is round-half-to-even; the crop centre lands on .5 often
  // enough (an even frame, a centred window) that it matters.
  const x = Math.min(Math.max(0, roundHalfEven(crop.x * width - w / 2)), width - w);
  const y = Math.min(Math.max(0, roundHalfEven(crop.y * height - h / 2)), height - h);
  return { w, h, x: Math.floor(x / 2) * 2, y: Math.floor(y / 2) * 2 };
}

function roundHalfEven(v: number): number {
  const floor = Math.floor(v);
  const diff = v - floor;
  if (Math.abs(diff - 0.5) > 1e-9) return Math.round(v);
  return floor % 2 === 0 ? floor : floor + 1;
}

/** The cues showing at output time `t`. */
export function cuesAt<C extends ClipCue>(cues: C[], t: number): C[] {
  return cues.filter((c) => t >= c.t0 && t < c.t1 && c.text.trim());
}

/** `m:ss.s` — what every time field shows. */
export function formatTime(seconds: number): string {
  const safe = Math.max(0, seconds);
  const m = Math.floor(safe / 60);
  const s = safe - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, '0')}`;
}

/** `1:02.5`, `62.5` or `1:02` → seconds; null when it is not a time. */
export function parseTime(text: string): number | null {
  const parts = text.trim().split(':');
  if (parts.length > 3 || parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) return null;
  return parts.reduce((total, p) => total * 60 + Number(p), 0);
}

/** A new cue at output time `t`, two seconds long or to the clip's end. */
export function cueAtPlayhead(edit: EditList, t: number): ClipCue {
  const end = outputDuration(edit);
  const t0 = Math.min(Math.max(0, t), Math.max(0, end - 0.5));
  // Floored, so a cue added at the playhead shows at the playhead (a seek lands a
  // hair before the frame it asked for).
  const start = Math.floor(t0 * 100) / 100;
  return { t0: start, t1: round2(Math.min(end, start + 2)), text: '' };
}

export const round2 = (v: number) => Math.round(v * 100) / 100;
/** Cut points are kept to the millisecond: a video element reports times like
 * 1.998122844827586, which is no use to anyone reading the edit list. */
const round3 = (v: number) => Math.round(v * 1000) / 1000;
