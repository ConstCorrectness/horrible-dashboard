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
"""

import os

if os.name == "nt":
    _parts = os.environ.get("PATH", "").split(os.pathsep)
    _clean = [p for p in _parts if "mingw" not in p.lower()]
    if len(_clean) != len(_parts):
        os.environ["PATH"] = os.pathsep.join(_clean)

os.environ.pop("SSLKEYLOGFILE", None)
