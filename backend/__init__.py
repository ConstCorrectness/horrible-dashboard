"""horrible-dashboard backend package.

Side effects on import, both guarding against the same abort —
``OPENSSL_Uplink(...): no OPENSSL_Applink`` on the first SSL context creation
(seen downstream as Vite proxy ``ECONNRESET``/``ECONNREFUSED``):

- On Windows, drop MinGW (MSYS2/Git) ``...\\mingw64\\bin`` dirs from the process
  PATH. Those ship an OpenSSL ``libcrypto`` built without the MSVC "applink"
  shim; if the interpreter loads it for TLS, the worker aborts.
- Drop ``SSLKEYLOGFILE`` (commonly set machine-wide for Wireshark). When set,
  CPython's ssl module uses OpenSSL's FILE*-based keylog API, which needs the
  applink shim ``python.exe`` doesn't provide — same abort, even with the
  correct libcrypto. The backend never needs TLS keylogging.

Running this at package import — before any module creates an SSL context —
keeps every launcher (uvicorn CLI, dev script, Tauri supervisor) safe.

Also loads ``.env`` from the repo root. ``scripts/dev.mjs`` already does this for
``pnpm dev``, but the documented bare ``uv run uvicorn backend.app:app`` command
does not — so without this, credentials like ``ATLAS_DB_USER`` are silently absent
depending on which launcher was used, which is a miserable thing to debug.
"""

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Read ``.env`` into the environment, without overriding what is already set.

    A real environment variable always wins over the file, so a launcher that
    already loaded ``.env`` (or an operator exporting a one-off override) is never
    second-guessed. Deliberately tiny: no interpolation, no `export` keyword, no
    multi-line values — this file holds flat credentials, and a dependency for
    that would be silly.
    """
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

if os.name == "nt":
    _parts = os.environ.get("PATH", "").split(os.pathsep)
    _clean = [p for p in _parts if "mingw" not in p.lower()]
    if len(_clean) != len(_parts):
        os.environ["PATH"] = os.pathsep.join(_clean)

os.environ.pop("SSLKEYLOGFILE", None)
