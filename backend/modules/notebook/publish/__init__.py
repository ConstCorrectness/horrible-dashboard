"""Publishing a notebook somewhere other people can read it.

The pipeline is always the same four steps, and the order is the point:

1. **Prepare a copy** (`prepare.py`). The user's file is never modified: stripping
   outputs or widget state happens to a deep copy.
2. **Preflight the copy** (`preflight.py`). Secrets, home-directory paths,
   tracebacks, widgets. Scanned *after* preparation, so "strip outputs" really does
   clear the findings that lived in outputs.
3. **Publish** through a target (`targets/`): GitHub Gist, GitHub Pages, Kaggle,
   Colab. A secret-shaped finding blocks until the person acknowledges it.
4. **Record** the result in `app.db` (`store.py`), so a second publish updates the
   same gist, page or kernel instead of scattering copies.

There is deliberately **no agent tool that publishes**. Putting a file on the
internet from a sentence is the `share.grant` problem again; the agent can list
publications, never make one. See docs/modules/notebook.mdx.
"""
