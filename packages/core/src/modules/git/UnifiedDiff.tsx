/**
 * A unified diff, rendered.
 *
 * This lives in the git module and is exported from its index because two
 * surfaces need it — the provenance pane's commit history and the IDE's source
 * control view — and a diff rendered two different ways in one app is a diff you
 * have to learn twice. It was a bare `<pre>` before; the classification is the
 * whole value, since an unstyled diff is a wall of text in which `-` and `+` are
 * one character wide.
 *
 * Deliberately dumb: it takes the text git printed and classifies each line. No
 * parsing into hunks and no intra-line word diff, because both are guesses about
 * a format git already decided, and both are wrong on the edge cases (a context
 * line that begins with `--`, a file whose content is itself a diff).
 */
import './git.css';

type LineKind = 'add' | 'del' | 'hunk' | 'file' | 'meta' | 'context';

/** Which kind of line this is. Order matters: the file headers `---`/`+++` must
 * be tested before the `-`/`+` they start with, or every diff opens with a
 * deleted and an added line that are really its header. */
function classify(line: string): LineKind {
  if (line.startsWith('@@')) return 'hunk';
  if (line.startsWith('+++') || line.startsWith('---')) return 'file';
  if (line.startsWith('diff --git') || line.startsWith('index ')) return 'meta';
  if (line.startsWith('new file') || line.startsWith('deleted file')) return 'meta';
  if (line.startsWith('rename ') || line.startsWith('similarity ')) return 'meta';
  if (line.startsWith('+')) return 'add';
  if (line.startsWith('-')) return 'del';
  return 'context';
}

export function UnifiedDiff({ diff, empty }: { diff: string; empty?: string }) {
  const lines = diff.split('\n');
  // A trailing newline yields a final empty line that would draw as a blank row.
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  if (lines.length === 0) {
    return <p className="git-diff-empty">{empty ?? 'No changes to show'}</p>;
  }
  return (
    <div className="git-diff" role="figure" aria-label="Diff">
      {lines.map((line, index) => (
        <div
          // Index as key: these rows are a static list rebuilt whenever the diff
          // text changes, never reordered.
          key={index}
          className={`git-diff-line git-diff-line--${classify(line)}`}
        >
          {line || ' '}
        </div>
      ))}
    </div>
  );
}
