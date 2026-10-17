"""Site import profiles: how to turn one website into a catalog + offline set.

A profile is a crawl recipe plus the shape of what it produces. The crawl half is
the docviewer's (start URLs, scope patterns, frontier, page cap, whether to keep the
page's scripts); the shape half is the knowledge base's (what kind each page is, how
pages nest into a course → chapter → lesson tree, which collections it lands in, and
the content selector that tells extraction where the article is).

Built-ins ship in `profiles.json`; user profiles live in `kb_profiles` in `app.db` so
the agent and the import dialog can add one without editing a file.

**Templates.** A profile can be parameterised by its `match` regex: named groups
become `{name}` placeholders in the other fields, so `apxml-course` matched against
`https://apxml.com/courses/vector-databases-semantic-search/...` yields a recipe
scoped to that one course. Placeholders are filled by name only — never with
`str.format`, because the same strings hold regex quantifiers like `{0,2}` — and a
value is `re.escape`d when it lands inside a pattern.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.modules.kb.store import get_db_conn, init_kb_db
from backend.modules.library.extract import css_to_xpath

_BUILTIN_PATH = Path(__file__).with_name("profiles.json")
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
# Fields that hold regexes (placeholder values are escaped there) vs. plain text.
_PATTERN_FIELDS = ("allow_patterns", "deny_patterns", "index_pages")
FRONTIERS = ("bfs", "sitemap", "llms_txt", "auto")
HIERARCHIES = ("flat", "url_path", "links")


@lru_cache(maxsize=1)
def _builtins() -> tuple[dict[str, Any], ...]:
    raw = json.loads(_BUILTIN_PATH.read_text(encoding="utf-8"))
    return tuple({**p, "builtin": True} for p in raw)


def _init_table() -> None:
    init_kb_db()
    with get_db_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kb_profiles (id TEXT PRIMARY KEY, spec TEXT)"
        )


def _user_profiles() -> list[dict[str, Any]]:
    _init_table()
    with get_db_conn() as conn:
        rows = conn.execute("SELECT spec FROM kb_profiles ORDER BY id").fetchall()
    return [{**json.loads(r["spec"]), "builtin": False} for r in rows]


def list_profiles() -> list[dict[str, Any]]:
    """Built-ins first, then user profiles. A user profile may not shadow a
    built-in id (`save_profile` refuses), so ids are unique across both."""
    return [dict(p) for p in _builtins()] + _user_profiles()


def get_profile(profile_id: str) -> dict[str, Any] | None:
    return next((p for p in list_profiles() if p["id"] == profile_id), None)


def validate(spec: dict[str, Any]) -> dict[str, Any]:
    """Normalise a profile and raise `ValueError` on anything that would fail
    later, mid-crawl, where the error is much harder to read."""
    out = {k: v for k, v in spec.items() if k != "builtin"}
    pid = str(out.get("id") or "")
    if not _ID_RE.match(pid):
        raise ValueError("id must be 2-63 chars of lowercase letters, digits and '-'")
    if not out.get("label"):
        out["label"] = pid
    starts = out.get("start_urls") or []
    if not starts or not all(isinstance(u, str) and u for u in starts):
        raise ValueError("start_urls must be a non-empty list of URLs")
    if not out.get("allow_domains"):
        raise ValueError("allow_domains is required — a crawl must not wander off-site")
    for field in ("match", *_PATTERN_FIELDS):
        values = out.get(field) or []
        if not isinstance(values, list):
            raise ValueError(f"{field} must be a list of regexes")
        for pattern in values:
            # Validate with placeholders filled by a harmless token.
            probe = (
                _PLACEHOLDER.sub(lambda m: "x", pattern)
                if field != "match"
                else pattern
            )
            try:
                re.compile(probe)
            except re.error as exc:
                raise ValueError(f"{field}: bad regex {pattern!r}: {exc}") from exc
    out["frontier"] = out.get("frontier") or "auto"
    if out["frontier"] not in FRONTIERS:
        raise ValueError(f"frontier must be one of {FRONTIERS}")
    out["hierarchy"] = out.get("hierarchy") or "flat"
    if out["hierarchy"] not in HIERARCHIES:
        raise ValueError(f"hierarchy must be one of {HIERARCHIES}")
    if out["hierarchy"] == "url_path" and not out.get("path_root"):
        raise ValueError(
            "hierarchy 'url_path' needs path_root (e.g. /courses/{course})"
        )
    if out.get("content_selector"):
        css_to_xpath(str(out["content_selector"]))  # raises on an unsupported selector
    out["reader_view"] = bool(out.get("reader_view", False))
    if out["reader_view"] and not out.get("content_selector"):
        raise ValueError("reader_view needs a content_selector to show")
    out["max_pages"] = max(1, min(int(out.get("max_pages") or 200), 2000))
    out["max_depth"] = max(0, min(int(out.get("max_depth") or 3), 10))
    out["keep_scripts"] = bool(out.get("keep_scripts", False))
    out.setdefault("page_kind", "article")
    out.setdefault("collections", [])
    out.setdefault("tags", [])
    return out


def save_profile(spec: dict[str, Any]) -> dict[str, Any]:
    clean = validate(spec)
    if any(p["id"] == clean["id"] for p in _builtins()):
        raise ValueError(f"{clean['id']!r} is a built-in profile; pick another id")
    _init_table()
    with get_db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO kb_profiles (id, spec) VALUES (?, ?)",
            (clean["id"], json.dumps(clean)),
        )
    return {**clean, "builtin": False}


def delete_profile(profile_id: str) -> bool:
    _init_table()
    with get_db_conn() as conn:
        cur = conn.execute("DELETE FROM kb_profiles WHERE id = ?", (profile_id,))
        return cur.rowcount > 0


def match(url: str) -> tuple[dict[str, Any], dict[str, str]] | None:
    """The first profile whose `match` regex hits `url`, with its named groups."""
    for profile in list_profiles():
        for pattern in profile.get("match") or []:
            m = re.search(pattern, url)
            if m:
                params = {k: v for k, v in m.groupdict().items() if v}
                return profile, params
    return None


def _fill(value: str, params: dict[str, str], *, regex: bool) -> str:
    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in params:
            return m.group(0)  # not a placeholder: a quantifier like {2} stays
        return re.escape(params[name]) if regex else params[name]

    return _PLACEHOLDER.sub(sub, value)


def missing_params(profile: dict[str, Any], params: dict[str, str]) -> list[str]:
    """Placeholder names the recipe needs that `params` doesn't supply."""
    names: set[str] = set()
    for field in ("start_urls", *_PATTERN_FIELDS):
        for value in profile.get(field) or []:
            names.update(_PLACEHOLDER.findall(value))
    for field in ("path_root", "sitemap_url"):
        if profile.get(field):
            names.update(_PLACEHOLDER.findall(str(profile[field])))
    # Regex quantifiers (`{2}`) never match the identifier pattern, so what's left
    # is a genuine placeholder.
    return sorted(n for n in names if n not in params)


def resolve(profile: dict[str, Any], params: dict[str, str]) -> dict[str, Any]:
    """A concrete crawl recipe: every placeholder filled. Raises `ValueError` when a
    parameterised profile is used without the parameters it needs."""
    missing = missing_params(profile, params)
    if missing:
        raise ValueError(
            f"profile {profile['id']!r} needs {', '.join(missing)} — pass a URL it "
            "matches, or the parameters explicitly"
        )
    out = dict(profile)
    for field in ("start_urls",):
        out[field] = [_fill(v, params, regex=False) for v in profile.get(field) or []]
    for field in _PATTERN_FIELDS:
        out[field] = [_fill(v, params, regex=True) for v in profile.get(field) or []]
    for field in ("path_root", "sitemap_url"):
        if profile.get(field):
            out[field] = _fill(str(profile[field]), params, regex=False)
    root = dict(profile.get("root") or {})
    for key in ("title", "url"):
        if root.get(key):
            root[key] = _fill(str(root[key]), params, regex=False)
    if root.get("title") and root["title"] in params.values():
        # A slug is a poor title; the crawl replaces it with the page's real one.
        root["title_is_slug"] = True
    out["root"] = root
    out["params"] = params
    return out
