import { describe, expect, it } from 'vitest';

import type { EditList } from '../api';
import {
  cropBox,
  cueAtPlayhead,
  cuesAt,
  formatTime,
  moveSegment,
  outputDuration,
  parseTime,
  removeSegment,
  segmentStarts,
  splitAt,
  toOutput,
  toSource,
  trim,
} from '../clip/edit';

function edit(over: Partial<EditList> = {}): EditList {
  return {
    version: 1,
    source: 'media/demo.mp4',
    page: '',
    segments: [
      { in: 1, out: 3 },
      { in: 5.5, out: 7.5 },
    ],
    crop: { aspect: 'source', x: 0.5, y: 0.5 },
    speed: 2,
    captions: [],
    overlays: [],
    audio: { replace: '', gain: 1, mute: false },
    output: { preset: 'x', gif_fps: 12, gif_width: 480, burn_captions: true },
    ...over,
  };
}

describe('clip timelines', () => {
  it('maps between source and output time across segments and speed', () => {
    const e = edit();
    expect(outputDuration(e)).toBe(2);
    expect(segmentStarts(e)).toEqual([0, 1]);
    expect(toOutput(e, 2)).toBe(0.5);
    expect(toOutput(e, 6.5)).toBe(1.5);
    expect(toOutput(e, 4)).toBeNull(); // cut out
    expect(toSource(e, 0.5)).toEqual({ index: 0, t: 2 });
    expect(toSource(e, 1.25)).toEqual({ index: 1, t: 6 });
    expect(toSource(e, 9)).toEqual({ index: 1, t: 7.5 }); // clamped to the end
  });

  it('splits, trims, removes and reorders segments', () => {
    const split = splitAt(edit(), 2);
    expect(split.segments).toEqual([
      { in: 1, out: 2 },
      { in: 2, out: 3 },
      { in: 5.5, out: 7.5 },
    ]);
    expect(splitAt(edit(), 1.05)).toEqual(edit()); // too close to the edge
    expect(splitAt(edit(), 4)).toEqual(edit()); // in no segment
    expect(trim(edit(), 0, 'in', 2.95).segments[0]).toEqual({ in: 2.9, out: 3 });
    expect(trim(edit(), 1, 'out', 6).segments[1]).toEqual({ in: 5.5, out: 6 });
    expect(removeSegment(edit(), 0).segments).toEqual([{ in: 5.5, out: 7.5 }]);
    expect(moveSegment(edit(), 1, -1).segments[0]).toEqual({ in: 5.5, out: 7.5 });
    expect(moveSegment(edit(), 1, 1)).toEqual(edit());
  });

  it('crops exactly as the backend does (pinned against test_scrive_media.py)', () => {
    const vertical = { aspect: '9:16' as const, x: 0.5, y: 0.5 };
    expect(cropBox(vertical, 1920, 1080)).toEqual({ w: 606, h: 1080, x: 656, y: 0 });
    expect(cropBox({ ...vertical, x: 0 }, 1920, 1080).x).toBe(0);
    expect(cropBox({ ...vertical, x: 1 }, 1920, 1080).x).toBe(1314);
    expect(cropBox({ aspect: '16:9', x: 0.5, y: 0.5 }, 1080, 1920)).toEqual({
      w: 1080,
      h: 606,
      x: 0,
      y: 656,
    });
    expect(cropBox({ aspect: '1:1', x: 0.5, y: 0.5 }, 1920, 1080)).toEqual({
      w: 1080,
      h: 1080,
      x: 420,
      y: 0,
    });
    expect(cropBox({ aspect: 'source', x: 0.5, y: 0.5 }, 1919, 1081)).toEqual({
      w: 1918,
      h: 1080,
      x: 0,
      y: 0,
    });
  });

  it('reads and writes times, and places new cues inside the clip', () => {
    expect(formatTime(62.54)).toBe('1:02.5');
    expect(formatTime(3)).toBe('0:03.0');
    expect(parseTime('1:02.5')).toBe(62.5);
    expect(parseTime('62.5')).toBe(62.5);
    expect(parseTime('1:2:3')).toBe(3723);
    expect(parseTime('abc')).toBeNull();
    expect(parseTime('')).toBeNull();
    expect(cueAtPlayhead(edit(), 0.5)).toEqual({ t0: 0.5, t1: 2, text: '' });
    expect(cueAtPlayhead(edit(), 5)).toEqual({ t0: 1.5, t1: 2, text: '' });
    const cues = [
      { t0: 0, t1: 1, text: 'a' },
      { t0: 0.5, t1: 2, text: '  ' },
    ];
    expect(cuesAt(cues, 0.7).map((c) => c.text)).toEqual(['a']);
  });
});
