"""What version of a package a doc seed holds, and what version you actually have.

Two different questions, deliberately answered by two different halves of this module:

- **`resolve_latest`** asks a package registry what the current stable release is, so
  a crawl can stamp "these are transformers 4.44 docs" onto every chunk it writes.
  The version comes from the registry rather than from a URL regex because a docs URL
  is not a version claim: `huggingface.co/docs/transformers/index` serves whatever is
  current, and `/v4.30.0/` URLs are the *archive*, which the seeds deny.
- **`installed_versions`** asks the machine what is actually importable. A doc answer
  that disagrees with the user's installed package is worse than no answer — it reads
  as authoritative and is wrong in a way that costs a debugging session.

Neither ever raises. A registry that is down means an unversioned crawl, which is
exactly what the crawler did before this existed; an unreadable venv means one fewer
signal. Version awareness is an improvement on retrieval, never a precondition for it.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REGISTRIES = ("pypi", "npm", "github")

_RESOLVE_TIMEOUT_S = 8.0
# A release lands a few times a month at most, and a crawl asks once per seed per run.
_TTL_S = 6 * 3600

_cache: dict[tuple[str, str], tuple[float, str | None]] = {}


@dataclass(frozen=True)
class PackageRef:
    """The package a doc seed documents. `name` is registry-shaped: a distribution
    name for pypi/npm, `owner/repo` for github."""

    registry: str
    name: str
    # The importable distribution name, when it differs from the registry name
    # (`huggingface-hub` is imported as `huggingface_hub`, and pip records the
    # dashed form — but a github ref like `google/adk-python` records neither).
    dist: str = ""

    @property
    def dist_name(self) -> str:
        return self.dist or self.name.rsplit("/", 1)[-1]


def parse_package(spec: Any) -> PackageRef | None:
    """Read a seed's `package` field. Returns None for anything malformed — a bad
    package ref must degrade to an unversioned seed, never break its crawl."""
    if not isinstance(spec, dict):
        return None
    registry = str(spec.get("registry") or "").strip().lower()
    name = str(spec.get("name") or "").strip()
    if registry not in REGISTRIES or not name:
        return None
    if registry == "github" and "/" not in name:
        return None
    return PackageRef(registry=registry, name=name, dist=str(spec.get("dist") or ""))


def normalize_version(raw: str | None) -> str:
    """`v4.44.2` and ` 4.44.2 ` are the same claim; an empty one stays empty."""
    text = (raw or "").strip()
    if text[:1] in ("v", "V") and text[1:2].isdigit():
        text = text[1:]
    return text


def version_series(raw: str | None) -> str:
    """The `major.minor` a version belongs to, or "" if it isn't one.

    Documentation is written per series, not per patch: transformers 4.44.0 and
    4.44.2 ship the same docs, and treating them as different versions would report a
    mismatch on every patch release and make the signal useless.
    """
    text = normalize_version(raw)
    match = re.match(r"^(\d+)(?:\.(\d+))?", text)
    if not match:
        return ""
    major, minor = match.group(1), match.group(2)
    return f"{major}.{minor}" if minor is not None else major


def is_prerelease(raw: str | None) -> bool:
    """Alphas, betas, rcs and dev builds — never what a docs site serves as current."""
    text = normalize_version(raw).lower()
    return bool(
        re.search(r"(a|b|rc|alpha|beta|dev|pre)\d*$|[-+](alpha|beta|rc|dev)", text)
    )


# --- what the registry says is current ---------------------------------------


async def resolve_latest(ref: PackageRef, *, use_cache: bool = True) -> str | None:
    """The latest stable release of `ref`, or None if it can't be determined.

    Plain `httpx`, not `_fetch_guarded`: a package registry is a vendor API chosen by
    this codebase, the same leg as Tavily or Brave. The guard is for URLs that came
    from a seed, a search result or a crawl — attacker-influenced input — and it
    only accepts html/xml/text besides.
    """
    key = (ref.registry, ref.name)
    now = time.monotonic()
    if use_cache and (hit := _cache.get(key)) and now - hit[0] < _TTL_S:
        return hit[1]

    try:
        version = await _resolve(ref)
    except Exception:  # noqa: BLE001 — a registry outage is not a crawl failure
        logger.warning("couldn't resolve %s version for %s", ref.registry, ref.name)
        version = None

    _cache[key] = (now, version)
    return version


async def _resolve(ref: PackageRef) -> str | None:
    import httpx

    url, headers = _endpoint(ref)
    async with httpx.AsyncClient(timeout=_RESOLVE_TIMEOUT_S) as client:
        resp = await client.get(url, headers=headers, follow_redirects=True)
        resp.raise_for_status()
        payload = resp.json()
    return _read_version(ref.registry, payload)


def _endpoint(ref: PackageRef) -> tuple[str, dict[str, str]]:
    if ref.registry == "pypi":
        return f"https://pypi.org/pypi/{ref.name}/json", {}
    if ref.registry == "npm":
        return f"https://registry.npmjs.org/{ref.name}/latest", {}
    return (
        f"https://api.github.com/repos/{ref.name}/releases/latest",
        {"Accept": "application/vnd.github+json"},
    )


def _read_version(registry: str, payload: Any) -> str | None:
    """Pull the version out of a registry response. Pure — this is the part worth
    testing, and each registry buries it somewhere different."""
    if not isinstance(payload, dict):
        return None
    if registry == "pypi":
        # `info.version` is PyPI's own "latest", which already excludes pre-releases
        # unless every release is one — so a package mid-beta reports its beta rather
        # than nothing, which is the honest answer for a docs site serving it.
        raw = (
            (payload.get("info") or {}) if isinstance(payload.get("info"), dict) else {}
        ).get("version")
    elif registry == "npm":
        raw = payload.get("version")
    else:
        # A prerelease is excluded from `releases/latest` by GitHub itself, but a
        # draft is not — and a draft release names a tag nobody can install yet.
        if payload.get("draft"):
            return None
        raw = payload.get("tag_name") or payload.get("name")
    version = normalize_version(str(raw) if raw is not None else None)
    return version or None


# --- what is actually installed here -----------------------------------------


def installed_versions(dist_name: str) -> dict[str, str]:
    """`{where: version}` for every environment on this machine that has the package.

    "Where" is `backend` for the app's own env and `project:<id>` for a training
    project venv. Read from `*.dist-info` metadata on disk rather than by running the
    interpreter: spawning one subprocess per project per query would be slow, and
    `asyncio.create_subprocess_exec` is broken under uvicorn's `--reload` loop on
    Windows anyway.
    """
    name = _canonical_dist(dist_name)
    if not name:
        return {}

    out: dict[str, str] = {}
    for label, paths in _search_paths().items():
        if version := _version_in(name, paths):
            out[label] = version
    return out


def _canonical_dist(raw: str) -> str:
    """PEP 503 normalization — `huggingface_hub`, `huggingface-hub` and
    `Huggingface.Hub` all name one distribution, and only the normalized form can
    match a directory that could have been written in any of them."""
    return re.sub(r"[-_.]+", "-", (raw or "").strip()).lower()


def _search_paths() -> dict[str, list[Path]]:
    paths: dict[str, list[Path]] = {"backend": [Path(p) for p in sys.path if p]}
    try:
        from backend.modules.training import envs, projects

        for project in projects.list_projects():
            if not envs.venv_exists(project):
                continue
            site = _site_packages(envs.venv_dir(project))
            if site:
                paths[f"project:{project.id}"] = site
    except Exception:  # noqa: BLE001 — training is optional to this question
        logger.debug("couldn't enumerate training project venvs", exc_info=True)
    return paths


def _site_packages(venv: Path) -> list[Path]:
    """The venv's site-packages, whichever layout this OS uses."""
    windows = venv / "Lib" / "site-packages"
    if windows.is_dir():
        return [windows]
    return [p for p in sorted(venv.glob("lib/python*/site-packages")) if p.is_dir()]


def _version_in(canonical_name: str, paths: list[Path]) -> str | None:
    from importlib.metadata import DistributionFinder, distributions

    try:
        context = DistributionFinder.Context(path=[str(p) for p in paths])
        for dist in distributions(context=context):
            try:
                found = dist.metadata["Name"]
            except Exception:  # noqa: BLE001 — a broken METADATA is one skipped dist
                continue
            if found and _canonical_dist(str(found)) == canonical_name:
                return normalize_version(dist.version) or None
    except Exception:  # noqa: BLE001
        logger.debug("couldn't read distributions from %s", paths, exc_info=True)
    return None


def installed_mismatch(doc_version: str | None, dist_name: str) -> str | None:
    """The installed version a doc's version disagrees with, or None.

    Three-state on purpose, the same discipline the interpretability module applies to
    `gated`: None covers both "we don't know what's installed" and "it matches", and
    only a positive answer justifies down-ranking a hit. Nothing installed is *no
    signal*, not a mismatch — penalizing docs for a package the user hasn't installed
    yet would bury exactly the docs someone reads before installing it.
    """
    series = version_series(doc_version)
    if not series:
        return None
    found = installed_versions(dist_name)
    if not found:
        return None
    if any(version_series(v) == series for v in found.values()):
        return None
    return sorted(found.values())[0]
