"""Publishing infrastructure shared by every module that puts work on the internet.

Extracted from `backend/modules/notebook/publish/` when Scrive became the second
publisher, the same move `notebook_core` made: feature modules must not import each
other's internals, so what both need lives here as shared infrastructure.

- `scan` — the secret / home-path scanner behind every preflight. It reports "found
  these", never "clean"; see its docstring for why that wording is load-bearing.
- `github_pages` — resolve (or create) a public Pages repository and push a whole file
  set as **one commit** through the Git Data API.
- `errors.PublishError` — a failure worth showing the person verbatim.

What stays in each module: what a *document* is (cells vs. MyST blocks), how it is
prepared, and where its publications are recorded.
"""

from backend.publishing.errors import PublishError

__all__ = ["PublishError"]
