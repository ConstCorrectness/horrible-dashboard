"""Identity for the game server: JWT sessions + GitHub/Google OAuth.

Two ways a node authenticates on `/game-ws`:

- **JWT** — the production path. After signing in (GitHub or Google OAuth), the node
  holds a short-lived JWT this server signed; `resolve_token` verifies it and returns
  the account. The signing secret is `GAMES_JWT_SECRET` (env) or a per-install secret
  persisted under `$HORRIBLE_DATA_DIR`.
- **Dev token** — the token *is* the account id (provider `dev`). Kept on by default
  (`GAMES_ALLOW_DEV_AUTH`) so local play and tests need no OAuth setup; set the env to
  `0` to require real sign-in.

Both providers use the **device flow** (no callback server — ideal for a
desktop/headless app): ask for a code, the user enters it at the provider's device
page, then we poll for the token and read their profile.

- **GitHub**: no client secret needed. Configure `games.github.clientId` (or
  `GAMES_GITHUB_CLIENT_ID`).
- **Google**: requires an OAuth client of type **"TVs and Limited Input devices"**,
  and (unlike GitHub) its token poll requires the client secret — for that client
  type Google treats it as non-confidential, but we still keep it server-side.
  Configure `games.google.clientId` (or `GAMES_GOOGLE_CLIENT_ID`) plus
  `GAMES_GOOGLE_CLIENT_SECRET`. Accounts are keyed `google:<sub>`, so two different
  Gmail accounts are two distinct players.

**Client ids may come from a setting; client secrets never may.** An id is public by
design, but `GET /api/settings` hands the whole settings bag to the browser, and a
bundled game server shares `$HORRIBLE_DATA_DIR/settings.json` with the node — so a
secret parked there is readable by any page the node serves. Every `*_CLIENT_SECRET`
below is therefore read from the environment only.

There is also a **web (authorization-code) flow** — the one-click redirect the UI
prefers. GitHub runs it on the same OAuth App as its device flow, but **Google
cannot**: a "TVs and Limited Input devices" client has no redirect-URI field at all,
so pointing the redirect flow at it returns `Error 400: redirect_uri_mismatch` no
matter what callback we send. Google's web flow therefore needs its *own* client of
type **"Web application"**, with this server's callback
(`<public base>/auth/google/callback`) registered on it — configured separately as
`GAMES_GOOGLE_WEB_CLIENT_ID` / `GAMES_GOOGLE_WEB_CLIENT_SECRET`. When it isn't set
we report the web flow as unavailable, which makes the UI fall back to the device
flow rather than open a consent page that 400s.
"""

from __future__ import annotations

import functools
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import jwt

from backend.games_server import store

JWT_ALG = "HS256"
JWT_TTL_S = 30 * 24 * 3600  # 30 days


def _data_dir() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))


def _jwt_secret() -> str:
    """The signing secret: env override, else a persisted per-install random secret."""
    env = os.environ.get("GAMES_JWT_SECRET")
    if env:
        return env
    path = _data_dir() / "game_server_jwt_secret"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret, encoding="utf-8")
    return secret


def _dev_auth_allowed() -> bool:
    return os.environ.get("GAMES_ALLOW_DEV_AUTH", "1") != "0"


# ---- JWT -------------------------------------------------------------------


def issue_jwt(account_id: str, display_name: str) -> str:
    now = int(time.time())
    payload = {
        "sub": account_id,
        "name": display_name,
        "iat": now,
        "exp": now + JWT_TTL_S,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALG)


def verify_jwt(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return None


def resolve_token(token: str) -> dict[str, str] | None:
    """Map an auth token to `{account_id, display_name}`, or None if invalid.

    Tries a signed JWT first; falls back to dev-token (token == account id) when dev
    auth is allowed. The `accounts` table is populated at sign-in, and the leaderboard
    LEFT-JOINs it, so this stays off the SQLite path in the hot auth loop.
    """
    token = (token or "").strip()
    if not token:
        return None
    claims = verify_jwt(token)
    if claims and claims.get("sub"):
        account_id = str(claims["sub"])
        return {
            "account_id": account_id,
            "display_name": str(claims.get("name") or account_id),
        }
    if _dev_auth_allowed():
        return {"account_id": token, "display_name": token}
    return None


def _session(account_id: str, display_name: str, handle: str | None) -> dict[str, Any]:
    """The payload every sign-in returns: a JWT plus the account behind it.

    One builder for all four flows (GitHub, Google, local signup, local login) so
    the shape can't drift between them — the node stores this verbatim and the
    browser is handed only the `account` half.
    """
    return {
        "token": issue_jwt(account_id, display_name),
        "account": {
            "id": account_id,
            "display_name": display_name,
            "handle": handle,
        },
    }


def account_payload(account_id: str) -> dict[str, Any]:
    """The `account` half on its own, read fresh from the DB — what `GET /me`
    answers. The node uses it to learn a handle its stored token predates, and to
    see a callsign change made from another machine."""
    account = store.get_account(account_id) or {}
    return {
        "id": account_id,
        "display_name": str(account.get("display_name") or account_id),
        "handle": account.get("handle"),
    }


# ---- Local (email + password) accounts --------------------------------------
#
# The third way in, alongside the two OAuth providers: sign up with an address and
# a password. Everything downstream is identical — the same `accounts` row, the
# same handle, the same JWT — so a local account is a first-class player, not a
# guest tier. Only the credential check differs.

# scrypt from the standard library, not bcrypt/argon2: neither is a dependency of
# this project (bcrypt appears in the lock file only via the optional `chroma`
# extra, so it is absent from a default `uv sync`), and scrypt is memory-hard,
# which is the property that matters. Parameters are stored *in* the hash string
# so they can be raised later without invalidating existing rows.
_SCRYPT_N = 2**14  # 16 MB at r=8 — see _hash_raw on why not 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024

MIN_PASSWORD_LEN = 8


def _hash_raw(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    # `maxmem` must be passed explicitly: CPython defaults it to 32 MB, which is
    # *under* what n=2**15,r=8 needs, so the stronger parameters raise ValueError
    # rather than run. Setting it high enough leaves room to raise _SCRYPT_N later.
    import hashlib

    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=dklen,
        maxmem=_SCRYPT_MAXMEM,
    )


def hash_password(password: str) -> str:
    """`scrypt$n$r$p$<b64 salt>$<b64 hash>` — self-describing, so verify never has
    to guess the parameters a stored hash was made with."""
    import base64

    salt = os.urandom(16)
    dk = _hash_raw(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DKLEN)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check against a stored hash. A malformed or unknown-scheme
    hash is False, never an exception — a corrupt row must fail the login, not the
    request."""
    import base64
    import hmac

    try:
        scheme, n, r, p, salt_b64, dk_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(dk_b64)
        actual = _hash_raw(
            password, base64.b64decode(salt_b64), int(n), int(r), int(p), len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


@functools.cache
def _dummy_hash() -> str:
    """A hash of a random string nobody can log in with, used to spend the same
    scrypt time on an unknown address as on a real one. Without it, "no such
    account" returns in microseconds while a real account takes ~50ms, and that
    gap is an account-enumeration oracle for anyone who can time two requests.

    Computed on first use rather than at import: scrypt is deliberately slow, and
    every process that imports this module would otherwise pay for it whether or
    not it ever serves a login.
    """
    return hash_password(secrets.token_hex(16))


EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _valid_email(email: str) -> bool:
    import re

    return bool(re.match(EMAIL_RE, email.strip())) and len(email.strip()) <= 254


def signup_local(email: str, password: str, callsign: str = "") -> dict[str, Any]:
    """Create an email+password account. Raises ValueError with a user-facing
    message on bad input or a taken address/callsign.

    The account id is `local:<uuid4hex>` — **not** the email. People change their
    address; an id is referenced by ratings, replays and match history forever.
    """
    import uuid

    email = store.normalize_email(email)
    if not _valid_email(email):
        raise ValueError("that doesn't look like an email address")
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    if store.get_local_credentials(email) is not None:
        raise ValueError("an account already exists for that email")

    account_id = store.upsert_account("local", uuid.uuid4().hex, email.split("@")[0])
    if store.set_local_credentials(account_id, email, hash_password(password)) != "ok":
        # Lost a race against a simultaneous signup for the same address.
        raise ValueError("an account already exists for that email")

    # An explicit callsign is the signup form's; without one we derive a starting
    # handle from the address, exactly as the OAuth flows derive theirs, so every
    # account leaves sign-up with a callsign it can rename later.
    if callsign:
        outcome = set_account_handle(account_id, callsign)
        if outcome != "ok":
            raise ValueError(
                "that callsign is taken"
                if outcome == "taken"
                else "a callsign is 3-20 characters of a-z, 0-9, - or _"
            )
        handle: str | None = callsign.strip().lower()
    else:
        handle = store.ensure_handle(account_id, email.split("@")[0])

    display_name = handle or email.split("@")[0]
    return _session(account_id, display_name, handle)


def login_local(email: str, password: str) -> dict[str, Any]:
    """Check an email+password and mint a session. Raises ValueError on a bad
    pair — deliberately the *same* message either way, so the response can't be
    used to test whether an address has an account."""
    cred = store.get_local_credentials(email)
    if cred is None:
        # Spend the time anyway (see _dummy_hash) before failing.
        verify_password(password, _dummy_hash())
        raise ValueError("wrong email or password")
    if not verify_password(password, str(cred["password_hash"])):
        raise ValueError("wrong email or password")

    account_id = str(cred["account_id"])
    account = store.get_account(account_id) or {}
    handle = account.get("handle")
    display_name = str(handle or account.get("display_name") or account_id)
    return _session(account_id, display_name, handle)


def set_account_handle(account_id: str, handle: str) -> str:
    """Claim or rename a callsign. Returns 'ok', 'invalid' or 'taken'.

    Unlike `store.ensure_handle` — which auto-derives one and locks it — this is
    the deliberate, user-chosen rename, so it applies whether or not a handle is
    already set. Uniqueness is enforced by the DB index, not by a pre-read.
    """
    return store.set_handle(account_id, handle)


# ---- GitHub OAuth (device flow) -------------------------------------------

GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def _github_client_id() -> str:
    from backend.modules.settings.routes import get_value

    return str(
        os.environ.get("GAMES_GITHUB_CLIENT_ID", "")
        or get_value("games.github.clientId", "")
    )


async def github_device_start() -> dict[str, Any]:
    """Begin the device flow: returns the code the user enters at github.com."""
    import httpx

    client_id = _github_client_id()
    if not client_id:
        raise ValueError("games.github.clientId is not configured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            GITHUB_DEVICE_CODE_URL,
            data={"client_id": client_id, "scope": "read:user"},
            headers={"Accept": "application/json"},
        )
        res.raise_for_status()
        return (
            res.json()
        )  # device_code, user_code, verification_uri, interval, expires_in


async def github_device_poll(device_code: str) -> dict[str, Any]:
    """Poll once for the access token. Returns `{pending: True}` until the user has
    authorized, then upserts the account and returns `{token, account}`."""
    import httpx

    client_id = _github_client_id()
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        res.raise_for_status()
        data = res.json()
        if data.get("error") == "authorization_pending":
            return {"pending": True}
        if "access_token" not in data:
            return {"pending": True, "error": data.get("error")}
        access = data["access_token"]
        profile = await client.get(
            GITHUB_USER_URL,
            headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
        )
        profile.raise_for_status()
        return _finish_github(profile.json())


def _finish_github(profile: dict[str, Any]) -> dict[str, Any]:
    """Turn a GitHub profile into an account + our JWT (pure, so it's unit-testable)."""
    subject = str(profile.get("id"))
    login = str(profile.get("login") or "")
    display_name = login or str(profile.get("name") or f"gh-{subject}")
    account_id = store.upsert_account("github", subject, display_name)
    # The GitHub login *is* the handle — it's globally unique, so no collision dance.
    handle = store.ensure_handle(account_id, login or display_name)
    return _session(account_id, display_name, handle)


# ---- Google OAuth (device flow) ---------------------------------------------

GOOGLE_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _google_client_id() -> str:
    from backend.modules.settings.routes import get_value

    return str(
        os.environ.get("GAMES_GOOGLE_CLIENT_ID", "")
        or get_value("games.google.clientId", "")
    )


def _google_client_secret() -> str:
    # Env only, deliberately — see the module docstring: the settings bag is served to
    # the browser wholesale, so a client secret must never be readable as a setting.
    return str(os.environ.get("GAMES_GOOGLE_CLIENT_SECRET", ""))


async def google_device_start() -> dict[str, Any]:
    """Begin the Google device flow. Returns the same shape as the GitHub start
    (`verification_uri` normalized — Google's wire name is `verification_url`)."""
    import httpx

    client_id = _google_client_id()
    if not client_id:
        raise ValueError("games.google.clientId is not configured")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                GOOGLE_DEVICE_CODE_URL,
                data={"client_id": client_id, "scope": "email profile"},
                headers={"Accept": "application/json"},
            )
            res.raise_for_status()
            data = res.json()
    except httpx.HTTPError as exc:
        if exc.response is not None:
            try:
                err_data = exc.response.json()
                if "error_description" in err_data:
                    raise ValueError(
                        f"Google API error: {err_data['error_description']}"
                    )
                elif "error" in err_data:
                    raise ValueError(f"Google API error: {err_data['error']}")
            except (ValueError, json.JSONDecodeError):
                pass
        raise ValueError(f"Failed to communicate with Google: {exc}")

    # Normalize to the GitHub shape the node/browser already speak.
    data.setdefault(
        "verification_uri",
        data.get("verification_url") or "https://www.google.com/device",
    )
    return data  # device_code, user_code, verification_uri, interval, expires_in


async def google_device_poll(device_code: str) -> dict[str, Any]:
    """Poll once for the access token. Google signals pending with 4xx statuses
    (428 authorization_pending, 403 slow_down), so we parse rather than raise."""
    import httpx

    client_id = _google_client_id()
    client_secret = _google_client_secret()
    if not client_id or not client_secret:
        raise ValueError(
            "games.google.clientId / GAMES_GOOGLE_CLIENT_SECRET are not configured"
        )
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        data = res.json()
        if data.get("error") in ("authorization_pending", "slow_down"):
            return {"pending": True}
        if "access_token" not in data:
            return {"pending": True, "error": data.get("error")}
        try:
            profile = await client.get(
                GOOGLE_USERINFO_URL,
                headers={
                    "Authorization": f"Bearer {data['access_token']}",
                    "Accept": "application/json",
                },
            )
            profile.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"Failed to fetch Google profile: {exc}")
        return _finish_google(profile.json())


def _finish_google(profile: dict[str, Any]) -> dict[str, Any]:
    """Turn a Google profile into an account + our JWT (pure, so it's unit-testable).
    Display name prefers the profile name, then the email's local part — so two
    Gmail accounts read naturally on the ladder."""
    subject = str(profile.get("id"))
    email = str(profile.get("email") or "")
    local_part = email.split("@")[0] if email else ""
    display_name = str(profile.get("name") or local_part or f"g-{subject}")
    account_id = store.upsert_account("google", subject, display_name)
    # Google has no username, so the handle is the email's local part (before @);
    # these aren't globally unique, so ensure_handle resolves collisions.
    handle = store.ensure_handle(account_id, local_part or display_name)
    return _session(account_id, display_name, handle)


# ---- Web (authorization-code) OAuth ----------------------------------------
#
# The redirect flow behind the browser "Sign in with GitHub/Google" button: the
# user authorizes on the provider's normal consent page (no code typing) and the
# provider redirects back to *this server's* callback. Unlike the device flow,
# the code->token exchange needs a client secret, kept here server-side.

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def _github_client_secret() -> str:
    # Env only, deliberately — see the module docstring: the settings bag is served to
    # the browser wholesale, so a client secret must never be readable as a setting.
    return str(os.environ.get("GAMES_GITHUB_CLIENT_SECRET", ""))


def _google_web_client_id() -> str:
    """Google's **web** client id — a different OAuth client from the device one (see
    the module docstring: a limited-input client can't carry a redirect URI). Env only
    for the secret; the id may also come from a setting because an id is public."""
    from backend.modules.settings.routes import get_value

    return str(
        os.environ.get("GAMES_GOOGLE_WEB_CLIENT_ID", "")
        or get_value("games.google.webClientId", "")
    )


def _google_web_client_secret() -> str:
    # Env only, deliberately: the settings bag is served to the browser wholesale, so a
    # client secret must never be readable as a setting.
    return str(os.environ.get("GAMES_GOOGLE_WEB_CLIENT_SECRET", ""))


def providers_available() -> dict[str, dict[str, bool]]:
    """Which sign-in flows this server can actually run, from its configured OAuth
    credentials — so a client can disable (and explain) a provider button up front
    instead of discovering the gap after it has already opened a popup.

    GitHub's device flow needs only the client id; its web flow needs id + secret on
    the same OAuth App. Google's device flow needs its limited-input id + secret, and
    its web flow needs the *separate* web-application client — never the device one,
    which has no redirect URI and so always 400s.

    `local` (email + password) needs no configuration at all, so it is always
    available — which is the point: a server with no OAuth credentials set can still
    sign people up. It reports neither `device` nor `web` because it is neither; the
    `password` key is what a client checks."""
    gh_id, gh_secret = bool(_github_client_id()), bool(_github_client_secret())
    g_device = bool(_google_client_id()) and bool(_google_client_secret())
    g_web = bool(_google_web_client_id()) and bool(_google_web_client_secret())
    return {
        "github": {"device": gh_id, "web": gh_id and gh_secret},
        "google": {"device": g_device, "web": g_web},
        "local": {"password": True},
    }


def web_config_error(provider: str) -> str | None:
    """A human message if `provider`'s web flow isn't configured, else None — so the
    UI fails fast at start rather than after a round-trip to the provider."""
    if provider == "github":
        if not _github_client_id():
            return "games.github.clientId is not configured"
        if not _github_client_secret():
            return (
                "GAMES_GITHUB_CLIENT_SECRET is not configured "
                "(required for web sign-in)"
            )
        return None
    if provider == "google":
        if not _google_web_client_id() or not _google_web_client_secret():
            # Not a fallback to the device client on purpose: using it here is exactly
            # what produces Google's redirect_uri_mismatch. Reporting "unconfigured"
            # instead lets the caller fall back to the device flow, which does work.
            return (
                "Google one-click sign-in is not configured on this game server "
                "(needs a Web-application OAuth client: GAMES_GOOGLE_WEB_CLIENT_ID / "
                "GAMES_GOOGLE_WEB_CLIENT_SECRET)"
            )
        return None
    return f"unknown provider {provider!r}"


def web_authorize_url(provider: str, state: str, redirect_uri: str) -> str:
    """The provider consent URL to send the browser to (carrying our CSRF `state`)."""
    from urllib.parse import urlencode

    if provider == "github":
        client_id = _github_client_id()
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "read:user",
            "state": state,
        }
        return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"
    if provider == "google":
        client_id = _google_web_client_id()
        if not client_id:
            raise ValueError(
                "Google one-click sign-in is not configured "
                "(GAMES_GOOGLE_WEB_CLIENT_ID)"
            )
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "email profile",
            "state": state,
            "access_type": "online",
        }
        return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"
    raise ValueError(f"unknown provider {provider!r}")


async def web_exchange(provider: str, code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange an authorization `code` for the provider token, read the profile, and
    mint our account + JWT (reuses `_finish_github`/`_finish_google`)."""
    import httpx

    if provider == "github":
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    "client_id": _github_client_id(),
                    "client_secret": _github_client_secret(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            res.raise_for_status()
            data = res.json()
            access = data.get("access_token")
            if not access:
                raise ValueError(
                    data.get("error_description")
                    or data.get("error")
                    or "no access_token from GitHub"
                )
            profile = await client.get(
                GITHUB_USER_URL,
                headers={
                    "Authorization": f"Bearer {access}",
                    "Accept": "application/json",
                },
            )
            profile.raise_for_status()
            return _finish_github(profile.json())
    if provider == "google":
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    # The web client, matching the one the consent page was opened
                    # with — Google checks that code, client and redirect_uri agree.
                    "client_id": _google_web_client_id(),
                    "client_secret": _google_web_client_secret(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            res.raise_for_status()
            data = res.json()
            access = data.get("access_token")
            if not access:
                raise ValueError(
                    data.get("error_description")
                    or data.get("error")
                    or "no access_token from Google"
                )
            profile = await client.get(
                GOOGLE_USERINFO_URL,
                headers={
                    "Authorization": f"Bearer {access}",
                    "Accept": "application/json",
                },
            )
            profile.raise_for_status()
            return _finish_google(profile.json())
    raise ValueError(f"unknown provider {provider!r}")
