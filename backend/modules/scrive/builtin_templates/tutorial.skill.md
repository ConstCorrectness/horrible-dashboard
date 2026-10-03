A tutorial is judged by one thing: does the reader end up with the working result?

- Every step is something the reader **does**. Rename "Step 1" to the action
  ("Create the project", "Add authentication"), never "Step 1: Introduction".
- Code is complete and runnable as shown: imports included, no `...` the reader has to
  guess. Use a `{code-cell}` when the output is part of the lesson; a plain fenced
  block (with its language) otherwise.
- After every block of code, say what the reader should see. A tutorial without
  checkpoints fails silently.
- Keep explanation short and in place. Theory belongs in a deep-dive; link to it.
- Use `{tip}` for shortcuts and `{warning}` for steps that lose data or cost money —
  sparingly, at most one per section.
