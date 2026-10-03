# Book

Course notes, documentation, a book. Every page sits beside a sidebar listing every
other page, so pages are read in order and cross-referenced.

Voice: a patient teacher. Define a term before using it, then use it consistently.
Each page should stand alone well enough that a reader arriving from search can follow
it, and link back (with a MyST cross-reference) to where a prerequisite is explained.

Layout notes for writers:

- The sidebar lists the home page, then `pages/` in path order, then posts by date.
  Name pages so their path order is the reading order (`01-intro.md`, `02-…`).
- Headings `##` and `###` both appear in the page; keep them descriptive, since they
  are what a skimming reader navigates by.
- Admonitions (`{note}`, `{tip}`, `{warning}`) are styled prominently; use them for
  asides, not for the main line of argument.
- The title comes from the frontmatter. Do not repeat it as a `# Heading`.
