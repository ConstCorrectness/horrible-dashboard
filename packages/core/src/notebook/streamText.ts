/**
 * Merging kernel stream chunks the way the backend does.
 *
 * The wire carries stream *deltas*, and both notebook stores re-merge them so a
 * pane that is already open matches the document the backend is writing. That
 * makes the merge rule shared state: `notebook_core.session` applies exactly this
 * to the stored output, and a store that merged differently would render a
 * notebook that disagrees with the file on disk.
 */

/**
 * Apply `\r` to accumulated stream text the way a terminal does: it returns to
 * the start of the line, so only the last write of each line survives.
 *
 * A progress bar writes one frame per update, each ending in a carriage return —
 * one fine-tune's notebook held 118 KB of tqdm frames for three real lines.
 */
export function collapseCarriageReturns(text: string): string {
  if (!text.includes('\r')) return text;
  return text
    .split('\n')
    .map((line) => {
      if (!line.includes('\r')) return line;
      // A trailing `\r` parks the cursor without erasing, so keeping the empty
      // text after it would blank the last frame of a finished progress bar — and
      // the `\r` is kept, because it is what makes the *next* chunk overwrite this
      // frame rather than be appended to it.
      const frames = line.split('\r').filter(Boolean);
      const last = frames.length ? frames[frames.length - 1] : '';
      return line.endsWith('\r') ? `${last}\r` : last;
    })
    .join('\n');
}
