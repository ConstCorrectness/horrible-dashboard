import { describe, expect, it } from 'vitest';
import { parseDownloadInfo } from './download';

const build = {
  file: 'hAssault.exe',
  url: '/download/hAssault.exe',
  version: '0.2.1',
  commit: 'abc1234',
  sizeBytes: 133975040,
  sha256: 'c9417888',
  builtAt: '2026-11-10T02:09:31.953Z',
};

describe('the native client the server offers', () => {
  it('reads the build the server staged', () => {
    expect(parseDownloadInfo({ windows: build })).toEqual({
      url: '/download/hAssault.exe',
      version: '0.2.1',
      sizeBytes: 133975040,
    });
  });

  it('offers nothing when the deploy carries no client', () => {
    // A deploy from Linux or macOS answers `windows: null`; an older server has
    // no route at all and the fetch hands back whatever its 404 body was.
    for (const answer of [{ windows: null }, {}, null, undefined, 'Not Found', []]) {
      expect(parseDownloadInfo(answer)).toBeNull();
    }
  });

  it('will not link anywhere but this server’s own download path', () => {
    for (const url of [
      'https://elsewhere.example/hAssault.exe',
      '//elsewhere.example/hAssault.exe',
      '/download/../auth/login',
      '/api/hassault/rooms',
      'javascript:alert(1)',
      '',
      undefined,
    ]) {
      expect(parseDownloadInfo({ windows: { ...build, url } })).toBeNull();
    }
  });

  it('needs a real size, because the size is what the player is told', () => {
    for (const sizeBytes of [0, -1, Number.NaN, '133975040', undefined]) {
      expect(parseDownloadInfo({ windows: { ...build, sizeBytes } })).toBeNull();
    }
  });

  it('still offers a build that did not say its version', () => {
    expect(parseDownloadInfo({ windows: { ...build, version: undefined } })?.version).toBe('');
  });
});
