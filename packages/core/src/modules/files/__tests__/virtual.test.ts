import { describe, expect, it } from 'vitest';

import { bufferUriFor, isVirtualPath } from '../api';

describe('isVirtualPath', () => {
  it('does not treat a Windows drive letter as a scheme', () => {
    // The whole reason the scheme regex demands two or more characters: a
    // one-character scheme would route every file on a Windows root into a provider.
    expect(isVirtualPath('C:/Users/Horrible/notes.txt')).toBe(false);
    expect(isVirtualPath('C:\\Users\\Horrible\\notes.txt')).toBe(false);
    expect(isVirtualPath('D:/data')).toBe(false);
  });

  it('rejects ordinary posix and relative paths', () => {
    expect(isVirtualPath('/home/user/notes.txt')).toBe(false);
    expect(isVirtualPath('notes.txt')).toBe(false);
    expect(isVirtualPath('./rel/path')).toBe(false);
  });

  it('accepts a registered-looking virtual scheme', () => {
    expect(isVirtualPath('gdrive:/root')).toBe(true);
    expect(isVirtualPath('gdrive:/1a2b3c')).toBe(true);
  });
});

describe('bufferUriFor', () => {
  it('prefixes a filesystem path so the editor can resolve it', () => {
    expect(bufferUriFor('C:/repo/a.ts')).toBe('workspace-file:C:/repo/a.ts');
  });

  it('leaves a virtual path alone — it is already a URI', () => {
    expect(bufferUriFor('gdrive:/abc')).toBe('gdrive:/abc');
  });
});
