"""Drop-in directory for **bundled** backend plugins.

Each subpackage here (a directory with an ``__init__.py`` exposing a module-level
``PLUGIN``) is discovered and loaded at startup by ``backend.sdk.loader``. This
ships empty; add a package to bundle a first-party backend plugin, or install one
via a ``horrible.plugins`` entry point / the ``HORRIBLE_PLUGINS_DIR`` env var. See
docs/architecture/python-sdk.md.
"""
