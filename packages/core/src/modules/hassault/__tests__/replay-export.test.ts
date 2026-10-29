import { describe, it, expect } from 'vitest';
import { exportSize, looksLikeMp4, pickMp4Type, replayVideoName } from '../replay-export';

describe('replay .mp4 export', () => {
  it('records MP4 or nothing: a WebM-only shell is refused, not renamed', () => {
    const only =
      (...ok: string[]) =>
      (t: string) =>
        ok.includes(t);
    expect(pickMp4Type(only('video/mp4;codecs=avc1', 'video/mp4'))).toBe('video/mp4;codecs=avc1');
    expect(pickMp4Type(only('video/mp4'))).toBe('video/mp4');
    expect(pickMp4Type(only('video/webm;codecs=vp9', 'video/webm'))).toBeNull();
  });

  it('sizes the video even on both axes and no larger than 1920', () => {
    expect(exportSize(1280, 720)).toEqual({ width: 1280, height: 720 });
    // Odd panes round down to even, which 4:2:0 H.264 needs.
    expect(exportSize(1281, 721)).toEqual({ width: 1280, height: 720 });
    // A 4K canvas (a high-DPI pane) is scaled down, aspect kept.
    expect(exportSize(3840, 2160)).toEqual({ width: 1920, height: 1080 });
    const tall = exportSize(1000, 2500);
    expect(tall.height).toBe(1920);
    expect(tall.width % 2).toBe(0);
  });

  it('names the file after the map, safely', () => {
    const at = new Date(2026, 9, 29, 17, 5, 9);
    expect(replayVideoName('hd_dust2', at)).toBe('hd_dust2_replay-20261029-170509.mp4');
    expect(replayVideoName('a/b c', at)).toBe('a_b_c_replay-20261029-170509.mp4');
    expect(replayVideoName('', at)).toBe('demo_replay-20261029-170509.mp4');
  });

  it('knows an MP4 by its ftyp box', () => {
    const ftyp = new Uint8Array([0, 0, 0, 0x18, 0x66, 0x74, 0x79, 0x70, 0x69, 0x73, 0x6f, 0x6d]);
    expect(looksLikeMp4(ftyp)).toBe(true);
    // A WebM starts with the EBML magic instead.
    expect(looksLikeMp4(new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0, 0, 0, 0, 0, 0, 0, 0]))).toBe(
      false,
    );
  });
});
