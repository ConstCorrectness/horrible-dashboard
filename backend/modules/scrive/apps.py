"""Web apps inside a Scrive site: `apps/<name>/`, static files with an `index.html`.

An app is arbitrary JavaScript — written by the author, by the agent, or imported
from a Hugging Face Space — and the ones worth embedding (WebGPU demos) need a
real origin: Cache Storage to keep a gigabyte of weights between views, which an
opaque `sandbox` origin throws on. So apps are served from **their own origin**,
`http://scrive-apps.localhost:<port>/<site>/<app>/…`:

- Chromium (and WebView2) resolve every ``*.localhost`` name to loopback and treat it
  as a secure context, so WebGPU and Cache Storage work there.
- Its host differs from the app UI's (`localhost:5173`, `tauri.localhost`) and the
  API's (`127.0.0.1:<port>`), so it shares no cookies, no storage and no same-origin
  access with either.

`AppsGate` (pure ASGI, outside every route) enforces the split both ways: a request
on the apps host can only ever be an app file, and a request **from** the apps origin
(by its `Origin` or `Referer`) to anything else — an API route, the `/ws` socket — is
refused. CORS already keeps the app from reading API responses; the gate is what
stops it *sending* them (a form POST, a WebSocket, neither of which CORS covers).

Hosted mode has no wildcard loopback name, so `apps_origin` reports none there and
the editor says to preview locally.
"""

from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from starlette.responses import PlainTextResponse, RedirectResponse, Response

from backend.modules.scrive import store

APPS_DIR = "apps"
APPS_HOST = "scrive-apps.localhost"
APP_NAME = re.compile(r"^[A-Za-z0-9][\w.-]{0,63}$")
#: Where an imported Space records what it came from.
SOURCE_FILE = "SPACE.json"

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}

#: Injected first into every HTML file served from the apps origin, so the editor can
#: show an app's errors (its console is otherwise a cross-origin frame's, unreachable).
#: Never part of the published files, which are copied verbatim.
CONSOLE_SHIM = (
    "<script>(function(){if(parent===window)return;"
    "function s(l,a){try{parent.postMessage({scriveApp:1,level:l,text:Array.prototype.map.call(a,"
    "function(x){try{return typeof x==='string'?x:(x&&x.stack)||JSON.stringify(x)}catch(e){return String(x)}}"
    ").join(' ').slice(0,2000)},'*')}catch(e){}}"
    "['error','warn','log','info'].forEach(function(k){var o=console[k];"
    "console[k]=function(){s(k,arguments);return o.apply(console,arguments)}});"
    "addEventListener('error',function(e){s('error',[e.message+' ('+(e.filename||'')+':'+(e.lineno||0)+')'])});"
    "addEventListener('unhandledrejection',function(e){var r=e.reason;"
    "s('error',['Unhandled rejection: '+((r&&r.stack)||r)])});"
    "})();</script>"
)

_HEAD = re.compile(rb"<head[^>]*>", re.I)
_HTML = re.compile(rb"<html[^>]*>", re.I)


class AppError(ValueError):
    pass


def _host_of(headers: dict[bytes, bytes], name: bytes) -> str:
    raw = headers.get(name, b"").decode("latin-1").strip().lower()
    if name in (b"origin", b"referer"):
        raw = re.sub(r"^[a-z][\w+.-]*://", "", raw)
        raw = raw.split("/", 1)[0]
    # Strip the port (and an IPv6 literal's brackets).
    if raw.startswith("["):
        return raw[1 : raw.find("]")] if "]" in raw else raw
    return raw.rsplit(":", 1)[0] if ":" in raw else raw


def is_apps_host(host: str) -> bool:
    return host == APPS_HOST


def apps_origin(server: tuple[str, int] | None, client_host: str | None) -> str | None:
    """The origin apps are served from, for this backend — or None where there is
    none: a request from off this machine (hosted), which cannot reach a loopback
    name, or a server whose port is unknown."""
    if not server or not server[1] or (client_host or "") not in _LOOPBACK:
        return None
    return f"http://{APPS_HOST}:{server[1]}"


# --- files ------------------------------------------------------------------------


def apps_root(site: str) -> Path:
    return store.site_dir(site) / APPS_DIR


def app_dir(site: str, name: str) -> Path:
    if not APP_NAME.match(name):
        raise AppError(f"not an app name: {name!r}")
    return apps_root(site) / name


def resolve_file(site: str, name: str, rel: str) -> Path:
    """A file of one app. `rel` is URL-relative to the app; empty or a folder means
    its `index.html`. Nothing outside the app's own folder is reachable."""
    base = app_dir(site, name).resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"{name}")
    target = (base / unquote(rel).lstrip("/")).resolve()
    if not target.is_relative_to(base):
        raise AppError("path escapes the app")
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        raise FileNotFoundError(rel)
    return target


def hub_url_for_skipped(site: str, name: str, rel: str) -> str | None:
    """Where a file an import left on the Hub (weights) still lives, so an imported
    Space that loads `./models/x.onnx` keeps working here. Only paths `SPACE.json`
    lists as skipped, at the imported revision."""
    try:
        record = json.loads((app_dir(site, name) / SOURCE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, AppError, store.StoreError):
        return None
    wanted = unquote(rel).lstrip("/")
    skipped = {str(s.get("path")) for s in record.get("skipped") or [] if isinstance(s, dict)}
    space, revision = str(record.get("space") or ""), str(record.get("revision") or "main")
    if wanted not in skipped or not _SPACE_ID.match(space):
        return None
    return f"{HUB}/spaces/{space}/resolve/{revision}/{wanted}"


def with_console_shim(html: bytes) -> bytes:
    """`html` with the console shim as the first thing to run."""
    for pattern in (_HEAD, _HTML):
        m = pattern.search(html)
        if m:
            return html[: m.end()] + CONSOLE_SHIM.encode() + html[m.end() :]
    return CONSOLE_SHIM.encode() + html


def serve(path: str) -> Response:
    """One request on the apps host: `/<site>/<app>/<file…>`."""
    parts = PurePosixPath(path).parts[1:]  # drop the leading "/"
    if len(parts) < 2:
        return PlainTextResponse("Scrive apps: /<site>/<app>/", status_code=404)
    site, name = parts[0], parts[1]
    rest = "/".join(parts[2:])
    if len(parts) == 2 and not path.endswith("/"):
        # Relative URLs in index.html resolve against the folder only with the slash.
        return RedirectResponse(f"/{site}/{name}/", status_code=308)
    try:
        file = resolve_file(site, name, rest)
    except FileNotFoundError:
        left = hub_url_for_skipped(site, name, rest)
        if left:
            return RedirectResponse(left, status_code=302)
        return PlainTextResponse("not found", status_code=404)
    except (AppError, store.StoreError):
        return PlainTextResponse("not found", status_code=404)
    media = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    if file.suffix == ".mjs":
        media = "text/javascript"
    if file.suffix == ".wasm":
        media = "application/wasm"
    body = file.read_bytes()
    if media == "text/html":
        body = with_console_shim(body)
    return Response(
        body,
        media_type=media,
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


class AppsGate:
    """Pure ASGI: the apps host serves app files and nothing else; requests from the
    apps origin reach nothing else. See the module docstring."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            await self.inner(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        if is_apps_host(_host_of(headers, b"host")):
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            if scope.get("method") not in ("GET", "HEAD"):
                response: Response = PlainTextResponse("read-only", status_code=405)
            else:
                response = serve(scope.get("path") or "/")
            await response(scope, receive, send)
            return
        if is_apps_host(_host_of(headers, b"origin")) or is_apps_host(
            _host_of(headers, b"referer")
        ):
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await PlainTextResponse("not from an app", status_code=403)(
                scope, receive, send
            )
            return
        await self.inner(scope, receive, send)


# --- listing ------------------------------------------------------------------------

_TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)


def describe(site: str, name: str) -> dict[str, Any]:
    folder = app_dir(site, name)
    index = folder / "index.html"
    title = name
    if index.is_file():
        m = _TITLE.search(index.read_bytes()[:65536])
        if m:
            title = (
                re.sub(r"\s+", " ", m.group(1).decode("utf-8", "replace")).strip()
                or name
            )
    source: dict[str, Any] | None = None
    record = folder / SOURCE_FILE
    if record.is_file():
        try:
            source = json.loads(record.read_text(encoding="utf-8"))
        except ValueError:
            source = None
    files = [p for p in folder.rglob("*") if p.is_file()]
    return {
        "name": name,
        "title": title,
        "hasIndex": index.is_file(),
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "source": source,
    }


def list_apps(site: str) -> list[dict[str, Any]]:
    root = apps_root(site)
    if not root.is_dir():
        return []
    return [
        describe(site, child.name)
        for child in sorted(root.iterdir())
        if child.is_dir() and APP_NAME.match(child.name)
    ]


# --- making apps ----------------------------------------------------------------------

TEMPLATES = Path(__file__).parent / "app_templates"


def templates() -> list[str]:
    return sorted(p.name for p in TEMPLATES.iterdir() if (p / "index.html").is_file())


def create_app(site: str, name: str, template: str = "blank") -> dict[str, Any]:
    """A new app folder from a template. Refuses to overwrite an existing app."""
    if template not in templates():
        raise AppError(f"no app template {template!r} (have: {', '.join(templates())})")
    folder = app_dir(site, name)
    if folder.exists():
        raise FileExistsError(f"apps/{name}")
    for src in sorted((TEMPLATES / template).rglob("*")):
        if src.is_file():
            store.write_bytes_atomic(folder / src.relative_to(TEMPLATES / template), src.read_bytes())
    return describe(site, name)


def write_app_file(site: str, name: str, rel: str, content: str, overwrite: bool = False) -> str:
    """Write one text file of an app (the agent's way in). Returns its site path."""
    folder = app_dir(site, name).resolve()
    clean = PurePosixPath(rel.strip().lstrip("/"))
    if not clean.parts or any(p in ("..", ".") for p in clean.parts) or clean.name == SOURCE_FILE:
        raise AppError(f"not a file an app may hold: {rel!r}")
    target = (folder / clean.as_posix()).resolve()
    if not target.is_relative_to(folder):
        raise AppError("path escapes the app")
    if target.exists() and not overwrite:
        raise FileExistsError(f"apps/{name}/{clean.as_posix()}")
    store.write_bytes_atomic(target, content.encode("utf-8"))
    return f"{APPS_DIR}/{name}/{clean.as_posix()}"


#: Weights are fetched from the Hub by the app at runtime; copying a gigabyte of them
#: into a git-tracked site would be wrong twice over.
WEIGHT_SUFFIXES = {
    ".onnx", ".onnx_data", ".bin", ".safetensors", ".gguf", ".pt", ".pth", ".ckpt",
    ".h5", ".tflite", ".npz", ".pb", ".msgpack",
}
MAX_IMPORT_FILE = 25 * 1024 * 1024
MAX_IMPORT_TOTAL = 100 * 1024 * 1024
HUB = "https://huggingface.co"
_SPACE_ID = re.compile(r"^([A-Za-z0-9][\w.-]*)/([\w.-]+)$")


def space_id(arg: str) -> str:
    raw = arg.strip()
    m = _SPACE_ID.match(raw) or re.match(
        r"^https://(?:www\.)?huggingface\.co/spaces/([^/?#]+)/([^/?#]+)", raw
    )
    if not m or not _SPACE_ID.match(f"{m.group(1)}/{m.group(2)}"):
        raise AppError(f"not a Space id or URL: {arg!r}")
    return f"{m.group(1)}/{m.group(2)}"


async def import_space(
    client: Any, site: str, space: str, name: str | None = None
) -> dict[str, Any]:
    """Copy a **static** Space's files into `apps/<name>/` so it runs (and can be
    edited, and published) from this site. Weights and oversized files are skipped
    and listed; the source, revision and licence go in `SPACE.json`."""
    sid = space_id(space)
    name = name or sid.split("/")[1]
    folder = app_dir(site, name)
    if folder.exists():
        raise FileExistsError(f"apps/{name}")

    res = await client.get(f"{HUB}/api/spaces/{sid}")
    if res.status_code == 404:
        raise FileNotFoundError(f"no Space {sid} on the Hub")
    res.raise_for_status()
    info = res.json()
    sdk = str(info.get("sdk") or "")
    if sdk != "static":
        raise AppError(
            f"{sid} is a {sdk or 'non-static'} Space: it needs a server, so only an "
            "embed ({space}) can show it."
        )
    sha = str(info.get("sha") or "main")
    tree = await client.get(f"{HUB}/api/spaces/{sid}/tree/{sha}", params={"recursive": "true"})
    tree.raise_for_status()

    copied: list[str] = []
    skipped: list[dict[str, Any]] = []
    total = 0
    for entry in tree.json():
        if entry.get("type") != "file":
            continue
        rel = str(entry.get("path") or "")
        clean = PurePosixPath(rel)
        if not rel or any(p in ("..", ".") for p in clean.parts) or rel == ".gitattributes":
            continue
        size = int((entry.get("lfs") or {}).get("size") or entry.get("size") or 0)
        reason = ""
        if clean.suffix.lower() in WEIGHT_SUFFIXES:
            reason = "model weights (fetch them from the Hub at runtime)"
        elif size > MAX_IMPORT_FILE:
            reason = f"over {MAX_IMPORT_FILE // (1024 * 1024)} MB"
        elif total + size > MAX_IMPORT_TOTAL:
            reason = f"the import is capped at {MAX_IMPORT_TOTAL // (1024 * 1024)} MB"
        if reason:
            skipped.append({"path": rel, "size": size, "reason": reason})
            continue
        body = await client.get(f"{HUB}/spaces/{sid}/resolve/{sha}/{rel}", follow_redirects=True)
        body.raise_for_status()
        target = (folder / clean.as_posix()).resolve()
        if not target.is_relative_to(folder.resolve()):
            continue
        store.write_bytes_atomic(target, body.content)
        total += len(body.content)
        copied.append(rel)

    card = info.get("cardData") or {}
    record = {
        "space": sid,
        "revision": sha,
        "license": card.get("license") or "",
        "title": card.get("title") or sid,
        "url": f"{HUB}/spaces/{sid}",
        "files": copied,
        "skipped": skipped,
    }
    store.write_bytes_atomic(
        folder / SOURCE_FILE, (json.dumps(record, indent=2) + "\n").encode("utf-8")
    )
    return {**describe(site, name), "skipped": skipped}
