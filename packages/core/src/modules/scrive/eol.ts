/**
 * Which line ending a file uses, so the editor writes back the one it read.
 *
 * CodeMirror splits a document on any of `\r\n`, `\r`, `\n` and joins it back with
 * `\n` unless told otherwise. Left alone, opening and saving a CRLF file would
 * rewrite every line of it — invisible in the editor, a whole-file diff in git, and
 * a broken promise for the source-preserving writer. Majority wins: a file that
 * mixes endings is normalised towards the one it mostly uses.
 */
export type Eol = '\n' | '\r\n';

export function detectEol(text: string): Eol {
  const crlf = text.match(/\r\n/g)?.length ?? 0;
  const lf = (text.match(/\n/g)?.length ?? 0) - crlf;
  return crlf > lf ? '\r\n' : '\n';
}
