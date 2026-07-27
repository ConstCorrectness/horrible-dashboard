"""URL canonicalization and domain extraction. Pure functions, no I/O.

Two jobs that both come down to "are these the same thing":

- `canonical_url` is what makes dedupe across providers work. Tavily, Brave and our
  own crawl index will happily return the same article as three different strings
  (`http://` vs `https://`, `www.` vs not, a trailing slash, a campaign parameter
  glued on by whoever linked it). Without a canonical form the fusion step ranks the
  same page three times and the model reads it three times.
- `registrable_domain` is what makes "two independent sources" mean something. Three
  citations to `openai.com`, `www.openai.com` and `platform.openai.com` are one
  source wearing three hats, and the research verification pass has to say so.

Deliberately **not** a full PSL implementation. A real public-suffix list is a
network-fetched, constantly-changing data file; the multi-part suffixes below cover
the cases that actually show up in web results, and the failure mode when one is
missing is conservative in the right direction (two domains judged the same, so a
claim looks *less* corroborated than it is, never more).
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Campaign/analytics parameters that never change what a page *is*. Dropping them is
# what collapses the same article shared from six places into one result.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_name",
        "gclid",
        "gclsrc",
        "dclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "twclid",
        "ttclid",
        "yclid",
        "_hsenc",
        "_hsmi",
        "ref",
        "ref_src",
        "referrer",
        "source",
        "spm",
        "scid",
        "vero_id",
        "wickedid",
    }
)

# Two-label public suffixes common enough in web results to matter. Without these,
# `bbc.co.uk` and `theguardian.co.uk` would both reduce to `co.uk` and count as the
# same source — the one failure mode of this shortcut that is *not* conservative.
_MULTI_SUFFIXES = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "me.uk",
        "net.uk",
        "sch.uk",
        "com.au",
        "net.au",
        "org.au",
        "edu.au",
        "gov.au",
        "co.nz",
        "net.nz",
        "org.nz",
        "govt.nz",
        "ac.nz",
        "co.jp",
        "ne.jp",
        "or.jp",
        "ac.jp",
        "go.jp",
        "co.kr",
        "or.kr",
        "co.in",
        "net.in",
        "org.in",
        "ac.in",
        "gov.in",
        "com.br",
        "net.br",
        "org.br",
        "gov.br",
        "com.cn",
        "net.cn",
        "org.cn",
        "edu.cn",
        "gov.cn",
        "com.mx",
        "com.ar",
        "com.tr",
        "com.sg",
        "com.hk",
        "com.tw",
        "co.za",
        "org.za",
        "co.il",
        "ac.il",
        "gov.il",
        "com.pl",
        "com.es",
        "com.pt",
        "com.ua",
        "co.id",
        "or.id",
        "ac.id",
        "github.io",
        "gitlab.io",
        "readthedocs.io",
        "netlify.app",
        "vercel.app",
        "pages.dev",
        "herokuapp.com",
        "substack.com",
        "medium.com",
        "wordpress.com",
        "blogspot.com",
        "notion.site",
    }
)

_DEFAULT_PORTS = {"http": "80", "https": "443"}

# Index filenames that name the same resource as the directory containing them.
_INDEX_FILES = ("index.html", "index.htm", "index.php", "default.html")


def _strip_amp(path: str) -> str:
    """Undo the two AMP URL shapes: a `/amp` suffix and an `.amp` extension.

    Google's AMP viewer prefixes (`google.com/amp/s/...`) are left alone on purpose —
    unwrapping those means reconstructing a different origin, which is a guess, not a
    normalization.
    """
    for suffix in ("/amp", "/amp/"):
        if path.endswith(suffix) and len(path) > len(suffix):
            return path[: -len(suffix)] or "/"
    if path.endswith(".amp"):
        return path[: -len(".amp")]
    return path


def canonical_url(url: str) -> str:
    """A stable identity string for a URL, for dedupe and cache keys.

    Lowercases scheme and host, drops `www.`, a default port, the fragment, tracking
    parameters, a trailing slash and an index filename; sorts the surviving query
    parameters so parameter order can't fork the key. Returns the input unchanged if
    it can't be parsed — an unparseable string is still a usable dedupe key, and
    raising here would take down a whole search over one malformed result.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw

    # http and https are folded together. Providers genuinely return both forms of
    # the same page, and leaving them distinct means the model reads it twice and
    # fusion ranks it twice. Safe because a canonical URL is only ever a *key* — the
    # original string is what actually gets fetched.
    scheme = (
        "https"
        if (parts.scheme or "https").lower() in ("http", "https")
        else (parts.scheme or "").lower()
    )
    host = (parts.hostname or "").lower()
    if not host:
        return raw
    if host.startswith("www."):
        host = host[4:]

    netloc = host
    port = parts.port
    # Compared against the *original* scheme's default, since the scheme above was
    # folded to https — otherwise `http://x.com:80/` would keep a redundant `:80`.
    if port is not None and str(port) != _DEFAULT_PORTS.get(
        (parts.scheme or "https").lower()
    ):
        netloc = f"{host}:{port}"

    path = _strip_amp(parts.path or "/")
    for index_file in _INDEX_FILES:
        if path.endswith("/" + index_file):
            path = path[: -len(index_file)]
            break
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    if not path:
        path = "/"

    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS
        )
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def host_of(url: str) -> str:
    """The lowercased hostname, `www.` stripped. Empty string when unparseable."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def registrable_domain(url: str) -> str:
    """The domain that identifies *who published this*, for independence checks.

    `platform.openai.com/docs` and `openai.com/blog` both yield `openai.com`, so
    citing both is citing one source. Falls back to the full host when the URL has no
    dots (a bare hostname, an IP), which keeps the function total.
    """
    host = host_of(url)
    if not host or host.replace(".", "").isdigit():
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def same_source(a: str, b: str) -> bool:
    """Whether two URLs were published by the same party."""
    return registrable_domain(a) == registrable_domain(b)
