import { describe, expect, it } from 'vitest';

import { parseGithubUri, saveSource, sourceTitle } from '../sources';

describe('sourceTitle', () => {
  it('names a workspace file by its basename', () => {
    expect(sourceTitle('workspace-file:C:/repo/app.ts')).toBe('app.ts');
  });

  it('gives a Drive file a neutral title — its path is an id, not a name', () => {
    expect(sourceTitle('gdrive:/1a2b3c')).toBe('Drive file');
  });

  it('names a GitHub file by its path basename', () => {
    expect(sourceTitle('github:octocat/hello@main/src/app.py')).toBe('app.py');
  });
});

describe('parseGithubUri', () => {
  it('splits owner, repo, ref, and path', () => {
    expect(parseGithubUri('github:octocat/hello@main/src/app.py')).toEqual({
      owner: 'octocat',
      repo: 'hello',
      ref: 'main',
      path: 'src/app.py',
    });
  });

  it('keeps slashes in a nested path', () => {
    expect(parseGithubUri('github:o/r@main/a/b/c.py')?.path).toBe('a/b/c.py');
  });

  it('returns null for a malformed uri', () => {
    expect(parseGithubUri('github:nope')).toBeNull();
  });
});

describe('saveSource', () => {
  it('refuses to write a Drive file', async () => {
    await expect(saveSource('gdrive:/abc', 'edited')).rejects.toThrow(/read-only/i);
  });

  it('refuses to write a GitHub file', async () => {
    await expect(saveSource('github:o/r@main/a.py', 'edited')).rejects.toThrow(/read-only/i);
  });

  it('still refuses an unknown scheme', async () => {
    await expect(saveSource('bogus:thing', 'x')).rejects.toThrow(/Unsupported/);
  });
});
