A deep dive earns its length by building one idea carefully; the figure is its centre.

- Intuition before formalism. A reader who stops after "The intuition" should still
  leave with the idea.
- **The scene.** Write it with `scrive.writeScene` before the figure section. A scene is
  a TSX module whose default export is a component receiving `{ params }`; it may import
  only `react`, `three`, `@react-three/fiber` and `@react-three/drei`. It cannot load
  files by URL, so build geometry in code. Keep it calm: slow motion, a neutral
  background, one highlighted thing. Expose two to four meaningful tweaks through
  `:params:` (`{"speed": {"value": 1, "min": 0, "max": 3, "step": 0.1}}`).
- Reference the scene from the post as `{r3f} ../scenes/<name>.tsx` (posts live in
  `posts/`). Always tell the reader what to look at.
- Every display equation is followed by a sentence that names each symbol.
- Claims of fact get a citation (a link, or `{cite}` when the site has a bibliography).
