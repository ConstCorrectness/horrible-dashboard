"""horrible-dashboard backend package.

Side effect on import: on Windows, drop MinGW (MSYS2/Git) ``...\\mingw64\\bin``
dirs from the process PATH. Those ship an OpenSSL ``libcrypto`` built without the
MSVC "applink" shim; if they're on PATH, the interpreter can load that libcrypto
for TLS and abort the worker with ``OPENSSL_Uplink(...): no OPENSSL_Applink``
(seen downstream as Vite proxy ``ECONNRESET``/``ECONNREFUSED``). Running this at
package import — before any module creates an SSL context — forces Python's own
bundled OpenSSL to be used. No-op on non-Windows or a clean PATH.
"""

import os

if os.name == "nt":
    _parts = os.environ.get("PATH", "").split(os.pathsep)
    _clean = [p for p in _parts if "mingw" not in p.lower()]
    if len(_clean) != len(_parts):
        os.environ["PATH"] = os.pathsep.join(_clean)
