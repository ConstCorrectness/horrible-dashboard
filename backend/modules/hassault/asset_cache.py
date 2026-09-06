"""HorribleAssault 3D Asset Cache & Sync Manager.

Manages on-disk caching in `.cache/assets` and syncing into `apps/web/public/`
so large binary GLBs are kept out of git history while remaining instantly
available offline.


Deliberately **not** `assets.py`. That name belongs to the map catalog (the
AssaultCube install probe plus `load_map`, the chokepoint every map route, the
match server and the console resolve through). This module once overwrote it
wholesale, which took `install_root`/`list_maps` out from under six call sites at
once: `GET /api/hassault/status` 500'd, so the pane's loader died at 17% with
nothing but a fetch error to show for it. Two unrelated jobs, two modules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import httpx

from backend.paths import cache_dir, repo_root

logger = logging.getLogger(__name__)


def _find_manifest() -> Path | None:
    root = repo_root() or Path.cwd()
    candidates = [
        root / "assets" / "manifest.json",
        root
        / "packages"
        / "core"
        / "src"
        / "modules"
        / "hassault"
        / "assets.manifest.json",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def get_manifest_data() -> dict[str, Any]:
    p = _find_manifest()
    if not p:
        return {"version": 1, "baseUrl": "", "assets": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to parse assets manifest %s: %s", p, e)
        return {"version": 1, "baseUrl": "", "assets": {}}


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_assets_status() -> dict[str, Any]:
    """Inspect local files against the asset manifest."""
    root = repo_root() or Path.cwd()
    data = get_manifest_data()
    assets = data.get("assets", {})
    cache_base = cache_dir() / "assets"

    results: list[dict[str, Any]] = []
    all_ok = True

    for key, item in assets.items():
        dest = root / item.get("destination", f"apps/web/public/{item['filename']}")
        cache_dest = cache_base / item["filename"]

        installed = dest.is_file()
        installed_hash = sha256_file(dest) if installed else None
        cached = cache_dest.is_file()
        cached_hash = sha256_file(cache_dest) if cached else None

        valid = installed and (installed_hash == item.get("sha256"))
        if not valid:
            all_ok = False

        results.append(
            {
                "id": key,
                "filename": item["filename"],
                "category": item.get("category", "model"),
                "expected_size": item.get("size", 0),
                "expected_sha256": item.get("sha256", ""),
                "installed": installed,
                "installed_valid": valid,
                "cached": cached,
                "cached_valid": cached and (cached_hash == item.get("sha256")),
            }
        )

    return {
        "status": "ready" if all_ok else "missing_assets",
        "total_assets": len(assets),
        "all_valid": all_ok,
        "assets": results,
    }


async def sync_assets(force: bool = False) -> dict[str, Any]:
    """Sync missing or outdated assets from remote storage into cache and public folder."""
    root = repo_root() or Path.cwd()
    data = get_manifest_data()
    base_url = os.environ.get("HORRIBLE_ASSETS_BASE_URL") or data.get("baseUrl", "")
    assets = data.get("assets", {})
    cache_base = cache_dir() / "assets"
    cache_base.mkdir(parents=True, exist_ok=True)

    public_base = root / "apps" / "web" / "public"
    public_base.mkdir(parents=True, exist_ok=True)

    synced: list[str] = []
    errors: list[str] = []

    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        for key, item in assets.items():
            filename = item["filename"]
            expected_hash = item.get("sha256", "")
            dest = root / item.get("destination", f"apps/web/public/{filename}")
            cache_file = cache_base / filename

            # Check if destination already valid
            if not force and dest.is_file() and sha256_file(dest) == expected_hash:
                continue

            # Check if cache file has valid copy
            if cache_file.is_file() and sha256_file(cache_file) == expected_hash:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cache_file, dest)
                synced.append(filename)
                continue

            # Download from remote
            if not base_url:
                errors.append(
                    f"Cannot download {filename}: no remote baseUrl configured"
                )
                continue

            remote_url = f"{base_url.rstrip('/')}/{filename}"
            logger.info(
                "Downloading HorribleAssault asset %s from %s", filename, remote_url
            )
            try:
                resp = await client.get(remote_url)
                resp.raise_for_status()
                content = resp.content

                downloaded_hash = hashlib.sha256(content).hexdigest()
                if expected_hash and downloaded_hash != expected_hash:
                    logger.warning(
                        "Hash mismatch for downloaded %s (expected %s, got %s)",
                        filename,
                        expected_hash,
                        downloaded_hash,
                    )

                cache_file.write_bytes(content)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cache_file, dest)
                synced.append(filename)
            except Exception as exc:
                logger.error("Failed to download %s: %s", remote_url, exc)
                errors.append(f"{filename}: {exc}")

    return {
        "success": len(errors) == 0,
        "synced": synced,
        "errors": errors,
    }
