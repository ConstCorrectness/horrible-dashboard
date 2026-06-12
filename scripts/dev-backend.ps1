<#
.SYNOPSIS
  Run the FastAPI backend dev server with a sane OpenSSL.

.DESCRIPTION
  MSYS2/Git ship an OpenSSL (`...\mingw64\bin\libcrypto-3-x64.dll`) built without
  the MSVC "applink" shim. When `mingw64\bin` is on PATH, the uvicorn --reload
  process loads that libcrypto instead of Python's bundled one, and the worker
  aborts on the first TLS context init with:

      OPENSSL_Uplink(...): no OPENSSL_Applink

  which the Vite proxy then surfaces as `ECONNRESET` / `ECONNREFUSED`.

  This launcher removes the MinGW dirs from the *process* PATH (not your global
  PATH), so Python loads its own OpenSSL. Your system PATH is untouched.

.EXAMPLE
  ./scripts/dev-backend.ps1
  ./scripts/dev-backend.ps1 -Port 8001
#>
param(
  [int]$Port = 8000
)

$env:Path = ($env:Path -split ';' | Where-Object { $_ -and $_ -notmatch 'mingw64\\bin' }) -join ';'
# A set SSLKEYLOGFILE (e.g. for Wireshark) makes CPython's ssl module use
# OpenSSL's FILE*-based keylog API, which aborts python.exe with
# "no OPENSSL_Applink" on the first SSL context init. Clear it for this process.
$env:SSLKEYLOGFILE = $null
# --reload-dir: only watch backend code. Watching the repo root means every
# $HORRIBLE_DATA_DIR write (dashboard layout, plugin storage) restarts the server.
uv run uvicorn backend.app:app --reload --reload-dir backend --port $Port
