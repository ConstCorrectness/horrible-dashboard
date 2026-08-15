"""Persistent accounts + the ELO ladder for the game server.

SQLite at `$HORRIBLE_DATA_DIR/game_server.db` (separate from a node's own data). Three
tables: **accounts** (who played), **ratings** (per-account, per-game ELO + W/L/D), and
**results** (a log of finished games). The referee calls `record_result` on `game_over`;
the leaderboard reads `ratings`.

Ratings are 2-player zero-sum ELO for now (tic-tac-toe/chess/etc.); multi-player games
(poker) will need a pairwise or placement-based extension.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from backend.games_server import crypto as _crypto
from backend import paths

BASE_RATING = 1200.0
ELO_K = 32.0

# Rated games before a player's tier (and exact rating) shows on the ladder.
PLACEMENT_GAMES = 5

# Tier floors, lowest first. Your tier is the highest floor at or below your rating.
TIERS: list[tuple[str, float]] = [
    ("bronze", 0),
    ("silver", 1100),
    ("gold", 1250),
    ("platinum", 1400),
    ("diamond", 1550),
    ("master", 1700),
    ("grandmaster", 1850),
]

# Which tier unlocks each game difficulty on the ranked queue.
#
# This is a *derivation* table, not a menu. The queue used to ask players which
# difficulty they wanted and reject the ones their tier had not unlocked, which
# split one thin pool three ways to gate a knob only task games even read. Now
# `derive_difficulty` reads it in reverse: your rating picks your difficulty, the
# way ELO already picks your opponent. The gates survive as the thing the UI shows
# you climbing towards.
DIFFICULTY_GATES: dict[str, str] = {
    "standard": "bronze",
    "hard": "gold",
    "expert": "diamond",
}


def tier_for(rating: float, placement_games: int) -> str:
    """A player's tier name, or `"placement"` until their placement matches are in."""
    if placement_games < PLACEMENT_GAMES:
        return "placement"
    tier = TIERS[0][0]
    for name, floor in TIERS:
        if rating >= floor:
            tier = name
    return tier


def tier_at_least(tier: str, gate: str) -> bool:
    """True if `tier` meets or exceeds `gate`. `"placement"` is below every tier —
    callers that always allow the base difficulty check that themselves."""
    order = [name for name, _floor in TIERS]
    if tier not in order or gate not in order:
        return False
    return order.index(tier) >= order.index(gate)


def derive_difficulty(rating: float, placement_games: int) -> str:
    """The difficulty a player of this strength plays at — the hardest one their
    tier has unlocked. Players in placement always get `standard`: their rating is
    not yet meaningful, so reading a difficulty off it would be noise."""
    tier = tier_for(rating, placement_games)
    if tier == "placement":
        return "standard"
    # Hardest first, so the first gate the tier clears wins.
    for name, gate in sorted(
        DIFFICULTY_GATES.items(),
        key=lambda kv: [t for t, _f in TIERS].index(kv[1]),
        reverse=True,
    ):
        if tier_at_least(tier, gate):
            return name
    return "standard"


def delta_preview(rating: float, opponent_rating: float) -> dict[str, int]:
    """What a win/draw/loss against an opponent of this rating would move you.

    The same ELO the referee applies after the game (`record_result`), run forwards
    so the Fight button can show the stakes *before* you commit. Rounded the way the
    displayed rating is, so the preview and the post-game toast agree.
    """
    expected = _expected(rating, opponent_rating)
    return {
        outcome: round(ELO_K * (score - expected))
        for outcome, score in (("win", 1.0), ("draw", 0.5), ("loss", 0.0))
    }


def get_db_path() -> Path:
    return paths.data_dir() / "game_server.db"


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Idempotent `ALTER TABLE ... ADD COLUMN` (SQLite has no IF NOT EXISTS for it)."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _m1_identity_and_series(conn: sqlite3.Connection) -> None:
    """Player handles + server-hosted bot accounts; series/ruleset/model metadata on
    the results log."""
    _add_column(conn, "accounts", "handle", "TEXT")
    _add_column(conn, "accounts", "is_bot", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "results", "series_id", "TEXT")
    _add_column(conn, "results", "ruleset", "TEXT")
    _add_column(conn, "results", "models", "TEXT")


def _m2_replays(conn: sqlite3.Connection) -> None:
    """Server-stored match replays: one row per game plus its ordered event log
    (public states, actions, and each seat's uploaded reasoning trace)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS replays (
            id         TEXT PRIMARY KEY,
            game_id    TEXT NOT NULL,
            table_id   TEXT NOT NULL,
            series_id  TEXT,
            created_at REAL NOT NULL,
            seats      TEXT NOT NULL,   -- json list of account ids, seat order
            ruleset    TEXT,            -- json Ruleset the match was played under
            models     TEXT,            -- json seat->model_label declarations
            winner     INTEGER,
            returns    TEXT,            -- json seat->payoff
            public     INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS replay_events (
            replay_id TEXT NOT NULL,
            idx       INTEGER NOT NULL,
            seat      INTEGER,
            kind      TEXT NOT NULL,
            payload   TEXT NOT NULL,
            PRIMARY KEY (replay_id, idx)
        )
        """
    )


# Ordered, additive-only migrations. The DB's `schema_meta.version` is how many of
# these have run; append — never reorder or edit a shipped entry (the live Fly
# volume upgrades by replaying the tail of this list).
def _m3_ladder(conn: sqlite3.Connection) -> None:
    """Placement matches + best-of-N series. Existing rated players are veterans —
    backfill them as already placed so nobody gets re-placed."""
    _add_column(conn, "ratings", "placement_games", "INTEGER NOT NULL DEFAULT 0")
    conn.execute("UPDATE ratings SET placement_games = MIN(games, 5)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series (
            id         TEXT PRIMARY KEY,
            game_id    TEXT NOT NULL,
            best_of    INTEGER NOT NULL,
            ruleset    TEXT,
            seats      TEXT NOT NULL,   -- json list of account ids
            wins       TEXT NOT NULL,   -- json list of per-seat win counts
            winner     INTEGER,
            created_at REAL NOT NULL
        )
        """
    )


def _m4_unique_handles(conn: sqlite3.Connection) -> None:
    """Player handles are unique (partial index: NULL handles don't collide)."""
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_handle "
        "ON accounts(handle) WHERE handle IS NOT NULL"
    )


def _m5_task_bank(conn: sqlite3.Connection) -> None:
    """The task bank for code games (bug hunts, golf): curated + generated tasks
    with hidden solutions, and which accounts have already seen which task."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_bank (
            id         TEXT PRIMARY KEY,
            kind       TEXT NOT NULL,      -- bug_hunt | golf | arena
            difficulty TEXT NOT NULL DEFAULT 'standard',
            payload    TEXT NOT NULL,      -- json: what players may see
            hidden     TEXT NOT NULL,      -- json: hidden tests / solution
            source     TEXT NOT NULL,      -- builtin | generated
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_plays (
            account_id TEXT NOT NULL,
            task_id    TEXT NOT NULL,
            played_at  REAL NOT NULL,
            PRIMARY KEY (account_id, task_id)
        )
        """
    )


def _m6_backfill_handles(conn: sqlite3.Connection) -> None:
    """One-time backfill: give every human account that predates auto-handles a
    handle derived from its OAuth username. Only `display_name` is persisted (no
    stored email), and for GitHub that column *is* the login; for Google it's the
    profile name (or already the email local part), so we fold `display_name` into
    the handle charset. Oldest-first for stable numbering; collisions get a numeric
    suffix. Bots keep their NULL handle (they fall back to display_name in the UI)."""
    rows = conn.execute(
        "SELECT id, display_name FROM accounts "
        "WHERE handle IS NULL AND is_bot = 0 ORDER BY created_at, id"
    ).fetchall()
    taken = {
        r["handle"]
        for r in conn.execute(
            "SELECT handle FROM accounts WHERE handle IS NOT NULL"
        ).fetchall()
    }
    for row in rows:
        base = _sanitize_handle(str(row["display_name"] or row["id"]))
        candidate, n = base, 2
        while candidate in taken:
            suffix = str(n)
            candidate = f"{base[: 20 - len(suffix)]}{suffix}"
            n += 1
        conn.execute(
            "UPDATE accounts SET handle = ? WHERE id = ?", (candidate, row["id"])
        )
        taken.add(candidate)


def _m7_local_credentials(conn: sqlite3.Connection) -> None:
    """Native email+password sign-in, alongside the OAuth providers.

    Deliberately a **separate table** rather than columns on `accounts`: the hash
    and the email then never ride along on the `SELECT *` in `get_account`, which
    feeds the account payloads handed to nodes. An account with no row here simply
    has no password — that's every OAuth account, and it's why the two kinds of
    sign-in can't be confused for one another.

    `email` is stored lowercased and uniquely indexed. It is *not* the account id:
    ids are `local:<uuid4hex>` because people change their email address and an id
    is forever.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS local_credentials (
            account_id    TEXT PRIMARY KEY,
            email         TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    REAL NOT NULL,
            updated_at    REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_local_credentials_email "
        "ON local_credentials(email)"
    )


def _m8_person_binding(conn: sqlite3.Connection) -> None:
    """Bind a game-server account to a peer-fabric **person**.

    The two identities existed side by side and never met: the game server owned
    the globally unique `handle` (it is the only thing all nodes agree on), while
    the fabric owned `person_id` — so `@rob` on the ladder and `HD-XXXX-…` in the
    friends roster were the same human with no way to know it. Binding them is
    what lets you find someone by the name they already have.

    Both directions are unique. One account is one person: sharing a handle across
    two person keys would make `@rob` ambiguous, and letting one person claim two
    handles would give them two names on the same ladder. Partial indexes, so the
    (many) unbound accounts don't collide on NULL.
    """
    _add_column(conn, "accounts", "person_id", "TEXT")
    _add_column(conn, "accounts", "person_public_key", "TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_person "
        "ON accounts(person_id) WHERE person_id IS NOT NULL"
    )


def _m9_rich_profiles(conn: sqlite3.Connection) -> None:
    """Profiles worth visiting: artwork, a status line, showcases, and a comment wall.

    A profile was four fields (avatar, bio, xp, and a derived level) and could only
    be read *by its owner* — there was no endpoint to fetch someone else's, which is
    why the Plaza's player card rendered a hardcoded placeholder bio for everyone.
    A profile nobody else can look at is not a profile.

    Three shapes here:

    - `avatar_url` / `background_url` alongside the existing `avatar`. The emoji
      column stays and stays 8 characters — it is a *fallback*, and the roster and
      Plaza render it inline where an image would be too heavy. What was broken was
      the frontend writing a base64 data URL into it and the server truncating that
      to 8 characters, so every upload silently became garbage. Images are files now
      (`profile_media`), and these hold a reference to one.
    - `profile_comments` — the Steam-shaped part. Keyed by account, not by person or
      session, precisely so a comment outlives its author being offline. `hidden`
      rather than a DELETE so a wall owner can moderate without destroying evidence.
    - `profile_media` — content-addressed blobs. `sha256` is the primary key, so the
      same image uploaded twice is stored once, and `account_id` records who put it
      there for quota and cleanup.
    """
    _add_column(conn, "player_profiles", "avatar_url", "TEXT")
    _add_column(conn, "player_profiles", "background_url", "TEXT")
    _add_column(conn, "player_profiles", "background_id", "TEXT")
    _add_column(conn, "player_profiles", "status_text", "TEXT NOT NULL DEFAULT ''")
    #: JSON: which showcases the owner has pinned, and in what order.
    _add_column(conn, "player_profiles", "showcase", "TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_comments (
            id         TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,   -- whose wall
            author_id  TEXT NOT NULL,   -- who wrote it
            body       TEXT NOT NULL,
            created_at REAL NOT NULL,
            hidden     INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profile_comments_wall "
        "ON profile_comments(account_id, created_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_media (
            sha256     TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            kind       TEXT NOT NULL,   -- 'avatar' | 'background'
            mime       TEXT NOT NULL,
            bytes      INTEGER NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profile_media_account "
        "ON profile_media(account_id)"
    )


MIGRATIONS: list[Any] = [
    _m1_identity_and_series,
    _m2_replays,
    _m3_ladder,
    _m4_unique_handles,
    _m5_task_bank,
    _m6_backfill_handles,
    _m7_local_credentials,
    _m8_person_binding,
    _m9_rich_profiles,
]


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_meta").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_meta (version) VALUES (0)")
        version = 0
    else:
        version = int(row["version"])
    for index in range(version, len(MIGRATIONS)):
        MIGRATIONS[index](conn)
        conn.execute("UPDATE schema_meta SET version = ?", (index + 1,))


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id           TEXT PRIMARY KEY,
                provider     TEXT NOT NULL,
                subject      TEXT NOT NULL,
                display_name TEXT NOT NULL,
                created_at   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ratings (
                account_id TEXT NOT NULL,
                game_id    TEXT NOT NULL,
                rating     REAL NOT NULL,
                wins       INTEGER NOT NULL DEFAULT 0,
                losses     INTEGER NOT NULL DEFAULT 0,
                draws      INTEGER NOT NULL DEFAULT 0,
                games      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (account_id, game_id)
            );
            CREATE TABLE IF NOT EXISTS results (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id    TEXT NOT NULL,
                table_id   TEXT NOT NULL,
                created_at REAL NOT NULL,
                winner     INTEGER,
                payload    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS challenge_scores (
                account_id TEXT NOT NULL,
                game_id    TEXT NOT NULL,
                correct    INTEGER NOT NULL,
                total      INTEGER NOT NULL,
                score      REAL NOT NULL,
                categories TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (account_id, game_id)
            );
            -- The Plaza (human social layer): gamified profiles + friendships.
            CREATE TABLE IF NOT EXISTS player_profiles (
                account_id TEXT PRIMARY KEY,
                avatar     TEXT NOT NULL DEFAULT '🙂',
                bio        TEXT NOT NULL DEFAULT '',
                xp         INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            -- One row per friendship, stored canonically (account_a < account_b).
            -- `requested_by` is the side that asked; `status` is pending|accepted.
            CREATE TABLE IF NOT EXISTS friendships (
                account_a    TEXT NOT NULL,
                account_b    TEXT NOT NULL,
                status       TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                updated_at   REAL NOT NULL,
                PRIMARY KEY (account_a, account_b)
            );
            """
        )
        _migrate(conn)


# ---- accounts --------------------------------------------------------------


def upsert_account(provider: str, subject: str, display_name: str) -> str:
    """Create or refresh an account; returns its stable id (`provider:subject`)."""
    init_db()
    account_id = f"{provider}:{subject}"
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO accounts (id, provider, subject, display_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name
            """,
            (account_id, provider, subject, display_name, time.time()),
        )
    return account_id


def get_account(account_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
    return dict(row) if row else None


# ---- local (email+password) credentials -------------------------------------


def normalize_email(email: str) -> str:
    """The stored form of an address: trimmed and lowercased. Applied on every
    read *and* write, so `Ada@Example.com` and `ada@example.com` are one account
    rather than two that can't both be created (the unique index would only catch
    the second one, and only if it happened to be written identically)."""
    return email.strip().lower()


def set_local_credentials(account_id: str, email: str, password_hash: str) -> str:
    """Attach an email+password to an account. Returns 'ok' or 'taken'.

    'taken' comes from the unique email index rather than a pre-read, so two
    simultaneous signups for the same address can't both pass a check and then
    both write.
    """
    init_db()
    now = time.time()
    with get_conn() as conn:
        try:
            conn.execute(
                """
                INSERT INTO local_credentials
                    (account_id, email, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    email = excluded.email,
                    password_hash = excluded.password_hash,
                    updated_at = excluded.updated_at
                """,
                (account_id, normalize_email(email), password_hash, now, now),
            )
        except sqlite3.IntegrityError:
            return "taken"
    return "ok"


def get_local_credentials(email: str) -> dict[str, Any] | None:
    """The credential row for an address, or None if nobody has signed up with it."""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM local_credentials WHERE email = ?",
            (normalize_email(email),),
        ).fetchone()
    return dict(row) if row else None


def find_account_by_email(email: str) -> dict[str, Any] | None:
    """The `accounts` row behind an email address, or None."""
    cred = get_local_credentials(email)
    return get_account(str(cred["account_id"])) if cred else None


HANDLE_RE = r"^[a-z0-9_-]{3,20}$"


def set_handle(account_id: str, handle: str) -> str:
    """Claim a unique player handle. Returns 'ok', 'invalid', or 'taken'."""
    import re

    handle = handle.strip().lower()
    if not re.match(HANDLE_RE, handle):
        return "invalid"
    init_db()
    with get_conn() as conn:
        # Accounts that only ever played with a dev token have no row yet.
        conn.execute(
            """
            INSERT INTO accounts (id, provider, subject, display_name, created_at)
            VALUES (?, 'dev', ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (account_id, account_id, account_id, time.time()),
        )
        try:
            conn.execute(
                "UPDATE accounts SET handle = ? WHERE id = ?", (handle, account_id)
            )
        except sqlite3.IntegrityError:
            return "taken"
    return "ok"


def _sanitize_handle(raw: str) -> str:
    """Fold a raw OAuth username (GitHub login / Google email local part) into the
    handle charset: lowercase, non-`[a-z0-9_-]` runs become a single `-`, trimmed,
    then padded/truncated to the 3-20 length HANDLE_RE requires."""
    import re

    base = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower()).strip("-_")
    if len(base) < 3:
        base = (base + "-player")[:20] if base else "player"
    return base[:20]


def ensure_handle(account_id: str, preferred: str) -> str:
    """Lock in an auto-derived handle from the account's OAuth username.

    Idempotent and stable: if the account already has a handle, it's returned
    unchanged (handles are locked once set). Otherwise `preferred` is sanitized and
    claimed; on a collision (two providers can share a username, e.g. Google email
    local parts) a numeric suffix is appended until one is free.
    """
    account = get_account(account_id)
    if account and account.get("handle"):
        return str(account["handle"])
    base = _sanitize_handle(preferred)
    candidate = base
    for n in range(2, 1000):
        if set_handle(account_id, candidate) == "ok":
            return candidate
        suffix = str(n)
        candidate = f"{base[: 20 - len(suffix)]}{suffix}"
    return base


# ---- person binding (game-server account ↔ peer-fabric person) --------------


#: Re-exported so a caller has one place to look for "the person primitives".
fingerprint_person = _crypto.fingerprint_person


def person_challenge(account_id: str, person_id: str) -> bytes:
    """The bytes a binding signature covers.

    It **includes the account id** on purpose: without it, a signature proving
    "I hold this person key" could be lifted from any other context and replayed
    to bind someone else's person to your account. Same canonical-JSON discipline
    as the peer wire and device certs, because signer and verifier are different
    machines running different code.
    """
    payload = {
        "purpose": "horrible.account.person",
        "account_id": account_id,
        "person_id": person_id,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def bind_person(account_id: str, person_id: str, person_public_key: str) -> str:
    """Attach a person identity to an account. Returns 'ok' or 'taken'.

    Idempotent for the same pair, so a node re-binding on every sign-in is free.
    'taken' comes from the unique index rather than a pre-read, so two concurrent
    binds can't both win.
    """
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT person_id FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if row is None:
            return "unknown-account"
        if row["person_id"] == person_id:
            return "ok"
        try:
            conn.execute(
                "UPDATE accounts SET person_id = ?, person_public_key = ? WHERE id = ?",
                (person_id, person_public_key, account_id),
            )
        except sqlite3.IntegrityError:
            return "taken"
    return "ok"


def _directory_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """The public slice of an account: enough to add someone, nothing more.

    Deliberately narrow — this is served to anyone who asks, so it carries no
    email, no token, no provider subject.
    """
    if row is None or not row["handle"]:
        return None
    return {
        "handle": row["handle"],
        "display_name": row["display_name"],
        "person_id": row["person_id"],
        "person_public_key": row["person_public_key"],
    }


def account_by_handle(handle: str) -> dict[str, Any] | None:
    """Resolve `@handle` to its public directory entry, or None."""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE handle = ?", (handle.strip().lower(),)
        ).fetchone()
    return _directory_row(row)


#: How many people one directory lookup may ask about. A roster is tens of rows;
#: this is comfortably above that and well below "enumerate everyone".
MAX_PERSON_LOOKUP = 100


def accounts_by_person(person_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The reverse of `account_by_handle`: fabric people → their ladder accounts.

    A node holds a roster keyed by `person_id` and needs to know which of those
    people are on the ladder — the direction `/directory/resolve` cannot answer,
    because it starts from a callsign the node does not yet have. Without it a node
    would have to guess handles to discover that a friend it already trusts is the
    same human as an account it already sees.

    Batched deliberately: reconciling a roster is inherently N lookups, and N
    requests to answer one question is how a cold start turns into a thundering
    herd. Capped so a caller cannot ask about the whole user base in one go, and it
    returns only `_directory_row`'s public slice — the same fields
    `/directory/resolve` already serves to anyone, so this exposes no new
    information, only a second index into it.

    Unmatched ids are simply absent from the result; a person with no ladder
    account is normal, not an error.
    """
    ids = [p.strip() for p in person_ids if p and p.strip()][:MAX_PERSON_LOOKUP]
    if not ids:
        return {}
    init_db()
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM accounts WHERE person_id IN ({placeholders})",  # noqa: S608 — placeholders are generated, ids are bound
            ids,
        ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = _directory_row(row)
        if entry is not None:
            # `account_id` is added on top of the public slice: a node needs it to
            # address the ladder's friend protocol, and it is not a secret (it rides
            # in every roster and match frame already).
            out[str(row["person_id"])] = {**entry, "account_id": row["id"]}
    return out


#: Shortest prefix `search_handles` will answer. Two characters would enumerate
#: the whole user base a few hundred queries at a time; this is a directory for
#: finding someone you can already half-name, not a member list.
MIN_SEARCH_PREFIX = 3


def search_handles(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Prefix-search handles — "an easier way to find people".

    Prefix, not substring: a substring match over a short query is close enough to
    a scrape, and `LIKE 'q%'` can use the handle index while `LIKE '%q%'` cannot.
    Bots are excluded; they aren't people you add.
    """
    q = query.strip().lower().lstrip("@")
    if len(q) < MIN_SEARCH_PREFIX:
        return []
    # LIKE wildcards are **escaped, not stripped**, and the length gate is applied
    # to the raw query above. Stripping them after the check let "%%%" through as
    # an empty prefix — `LIKE '%'`, i.e. every account in one request. And `_` is a
    # legal handle character, so stripping it would quietly make `rob_smith`
    # unfindable by anyone who typed the underscore.
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM accounts WHERE handle LIKE ? ESCAPE '\\' AND is_bot = 0 "
            "ORDER BY handle LIMIT ?",
            (escaped + "%", max(1, min(limit, 25))),
        ).fetchall()
    return [entry for row in rows if (entry := _directory_row(row)) is not None]


# ---- ratings ---------------------------------------------------------------


def _get_rating_row(
    conn: sqlite3.Connection, account_id: str, game_id: str
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM ratings WHERE account_id = ? AND game_id = ?",
        (account_id, game_id),
    ).fetchone()
    if row:
        return dict(row)
    return {
        "account_id": account_id,
        "game_id": game_id,
        "rating": BASE_RATING,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "games": 0,
        "placement_games": 0,
    }


def _write_rating(conn: sqlite3.Connection, r: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO ratings
            (account_id, game_id, rating, wins, losses, draws, games, placement_games)
        VALUES (:account_id, :game_id, :rating, :wins, :losses, :draws, :games,
                :placement_games)
        ON CONFLICT(account_id, game_id) DO UPDATE SET
            rating = excluded.rating, wins = excluded.wins,
            losses = excluded.losses, draws = excluded.draws, games = excluded.games,
            placement_games = excluded.placement_games
        """,
        r,
    )


def get_rating(account_id: str, game_id: str) -> dict[str, Any] | None:
    """A player's rating row for one game, or None if they've never played it."""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ratings WHERE account_id = ? AND game_id = ?",
            (account_id, game_id),
        ).fetchone()
    return dict(row) if row else None


def _expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def _score(payoff: float) -> float:
    """ELO actual-score from a zero-sum payoff: win=1, draw=0.5, loss=0."""
    return 1.0 if payoff > 0 else 0.0 if payoff < 0 else 0.5


def _is_bot(conn: sqlite3.Connection, account_id: str) -> bool:
    row = conn.execute(
        "SELECT is_bot FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    return bool(row and row["is_bot"])


def _seat_update(
    conn: sqlite3.Connection,
    seat: int,
    account_id: str,
    game_id: str,
    rating_row: dict[str, Any] | None,
    delta: float,
) -> dict[str, Any]:
    """The per-seat `rating_update` payload: new rating/tier plus fresh XP/level."""
    profile_row = conn.execute(
        "SELECT xp FROM player_profiles WHERE account_id = ?", (account_id,)
    ).fetchone()
    xp = int(profile_row["xp"]) if profile_row else 0
    update: dict[str, Any] = {
        "seat": seat,
        "account_id": account_id,
        "game_id": game_id,
        "xp": xp,
        "level": level_for_xp(xp),
    }
    if rating_row is not None:
        update.update(
            rating=round(rating_row["rating"], 1),
            delta=round(delta, 1),
            tier=tier_for(rating_row["rating"], rating_row["placement_games"]),
            placement_games=rating_row["placement_games"],
        )
    return update


def record_result(
    game_id: str,
    table_id: str,
    seats: list[str],
    returns: dict[int, float],
    winner: int | None,
    *,
    rated: bool = True,
    series_id: str | None = None,
    ruleset: dict[str, Any] | None = None,
    models_used: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Persist a finished game, update ratings, and return per-seat updates
    (rating/delta/tier/xp/level) for the server's `rating_update` pushes.

    Bots keep their **pinned** rating (their row is never rewritten) but still
    move their human opponent's rating — that's what makes placement-vs-bot
    calibration work. Unrated (casual) games log + grant XP only.
    """
    init_db()
    updates: list[dict[str, Any]] = []
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO results
                (game_id, table_id, created_at, winner, payload, series_id, ruleset, models)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                table_id,
                time.time(),
                winner,
                json.dumps({"seats": seats, "returns": returns}),
                series_id,
                json.dumps(ruleset) if ruleset else None,
                json.dumps(models_used) if models_used else None,
            ),
        )
        # Everyone who played earns XP toward their Plaza level (win 20 / draw 10 /
        # loss 5), so the social profile grows just by showing up and competing.
        for idx, account_id in enumerate(seats):
            if _is_bot(conn, account_id):
                continue
            payoff = returns.get(idx, 0.0)
            gained = 20 if payoff > 0 else 5 if payoff < 0 else 10
            _add_xp(conn, account_id, gained)
        # ELO is 2-player for now; multi-player rating is a later phase. Self-play
        # (same account on both seats) is never rated against itself.
        ratable = rated and len(seats) == 2 and seats[0] != seats[1]
        if not ratable:
            return [
                _seat_update(conn, idx, account_id, game_id, None, 0.0)
                for idx, account_id in enumerate(seats)
                if not _is_bot(conn, account_id)
            ]
        a, b = seats[0], seats[1]
        ra = _get_rating_row(conn, a, game_id)
        rb = _get_rating_row(conn, b, game_id)
        sa = _score(returns.get(0, 0.0))
        sb = _score(returns.get(1, 0.0))
        ea = _expected(ra["rating"], rb["rating"])
        eb = _expected(rb["rating"], ra["rating"])
        deltas = (ELO_K * (sa - ea), ELO_K * (sb - eb))
        for seat, (account_id, r, s, delta) in enumerate(
            ((a, ra, sa, deltas[0]), (b, rb, sb, deltas[1]))
        ):
            if _is_bot(conn, account_id):
                continue  # pinned: a bot's rating anchors its tier, never drifts
            r["rating"] += delta
            r["games"] += 1
            r["wins"] += 1 if s == 1.0 else 0
            r["losses"] += 1 if s == 0.0 else 0
            r["draws"] += 1 if s == 0.5 else 0
            r["placement_games"] = min(
                int(r.get("placement_games", 0)) + 1, PLACEMENT_GAMES
            )
            _write_rating(conn, r)
            updates.append(_seat_update(conn, seat, account_id, game_id, r, delta))
    return updates


def record_series(
    series_id: str,
    game_id: str,
    best_of: int,
    seats: list[str],
    wins: list[int],
    winner: int | None,
    ruleset: dict[str, Any] | None = None,
) -> None:
    """Persist a finished best-of-N series."""
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO series
                (id, game_id, best_of, ruleset, seats, wins, winner, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                series_id,
                game_id,
                best_of,
                json.dumps(ruleset) if ruleset else None,
                json.dumps(seats),
                json.dumps(wins),
                winner,
                time.time(),
            ),
        )


def record_challenge(account_id: str, game_id: str, report: dict[str, Any]) -> bool:
    """Persist a challenge attempt, keeping only the player's **best** (most correct)
    per game. Returns True if this attempt is a new best."""
    init_db()
    correct = int(report.get("correct", 0))
    with get_conn() as conn:
        row = conn.execute(
            "SELECT correct FROM challenge_scores WHERE account_id = ? AND game_id = ?",
            (account_id, game_id),
        ).fetchone()
        if row is not None and row["correct"] >= correct:
            return False  # not an improvement — keep the existing best
        conn.execute(
            """
            INSERT INTO challenge_scores
                (account_id, game_id, correct, total, score, categories, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, game_id) DO UPDATE SET
                correct = excluded.correct, total = excluded.total,
                score = excluded.score, categories = excluded.categories,
                updated_at = excluded.updated_at
            """,
            (
                account_id,
                game_id,
                correct,
                int(report.get("total", 0)),
                float(report.get("score", 0.0)),
                json.dumps(report.get("categories", {})),
                time.time(),
            ),
        )
    return True


def challenge_leaderboard(game_id: str, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.account_id, c.correct, c.total, c.score,
                   COALESCE(a.display_name, c.account_id) AS display_name
            FROM challenge_scores c
            LEFT JOIN accounts a ON a.id = c.account_id
            WHERE c.game_id = ?
            ORDER BY c.correct DESC, c.updated_at ASC
            LIMIT ?
            """,
            (game_id, limit),
        ).fetchall()
    return [
        {
            "account_id": r["account_id"],
            "display_name": r["display_name"],
            "correct": r["correct"],
            "total": r["total"],
            "score": round(r["score"], 3),
        }
        for r in rows
    ]


def leaderboard(game_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """The ranked ladder for a game. Bots are excluded (their ratings are pinned
    anchors, not achievements); players still in placement show at the bottom with
    their exact rating masked."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT r.account_id, r.rating, r.wins, r.losses, r.draws, r.games,
                   r.placement_games,
                   COALESCE(a.handle, a.display_name, r.account_id) AS display_name
            FROM ratings r
            LEFT JOIN accounts a ON a.id = r.account_id
            WHERE r.game_id = ? AND COALESCE(a.is_bot, 0) = 0
            ORDER BY (r.placement_games >= ?) DESC, r.rating DESC
            LIMIT ?
            """,
            (game_id, PLACEMENT_GAMES, limit),
        ).fetchall()
    entries = []
    for row in rows:
        tier = tier_for(row["rating"], row["placement_games"])
        placed = tier != "placement"
        entries.append(
            {
                "account_id": row["account_id"],
                "display_name": row["display_name"],
                "rating": round(row["rating"], 1) if placed else None,
                "tier": tier,
                "placement_games": row["placement_games"],
                "wins": row["wins"],
                "losses": row["losses"],
                "draws": row["draws"],
                "games": row["games"],
            }
        )
    return entries


# ---- practice bots -----------------------------------------------------------


def ensure_bot_account(
    account_id: str, display_name: str, game_id: str, pinned_rating: float
) -> None:
    """Create (or refresh) a server-hosted practice bot: an `is_bot` account with a
    **pinned** rating for `game_id` — the anchor human ratings calibrate against."""
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO accounts (id, provider, subject, display_name, created_at, is_bot)
            VALUES (?, 'bot', ?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name, is_bot = 1
            """,
            (account_id, account_id, display_name, time.time()),
        )
        _write_rating(
            conn,
            {
                "account_id": account_id,
                "game_id": game_id,
                "rating": pinned_rating,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "games": 0,
                "placement_games": PLACEMENT_GAMES,
            },
        )


# ---- replays ----------------------------------------------------------------


def save_replay(
    *,
    replay_id: str,
    game_id: str,
    table_id: str,
    seats: list[str],
    events: list[dict[str, Any]],
    series_id: str | None = None,
    ruleset: dict[str, Any] | None = None,
    models_used: dict[int, str] | None = None,
    winner: int | None = None,
    returns: dict[int, float] | None = None,
) -> str:
    """Persist a finished game's full event log (public states, actions, and each
    seat's reasoning trace). Participants can always view it; `publish_replay`
    opens it up."""
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO replays
                (id, game_id, table_id, series_id, created_at, seats, ruleset,
                 models, winner, returns, public)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                replay_id,
                game_id,
                table_id,
                series_id,
                time.time(),
                json.dumps(seats),
                json.dumps(ruleset) if ruleset else None,
                json.dumps(models_used) if models_used else None,
                winner,
                json.dumps(returns or {}),
            ),
        )
        conn.execute("DELETE FROM replay_events WHERE replay_id = ?", (replay_id,))
        conn.executemany(
            "INSERT INTO replay_events (replay_id, idx, seat, kind, payload) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    replay_id,
                    idx,
                    event.get("seat"),
                    str(event.get("kind") or "event"),
                    json.dumps(event),
                )
                for idx, event in enumerate(events)
            ],
        )
    return replay_id


def _replay_row_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "game_id": row["game_id"],
        "table_id": row["table_id"],
        "series_id": row["series_id"],
        "created_at": row["created_at"],
        "seats": json.loads(row["seats"]),
        "ruleset": json.loads(row["ruleset"]) if row["ruleset"] else None,
        "models": json.loads(row["models"]) if row["models"] else None,
        "winner": row["winner"],
        "returns": json.loads(row["returns"]) if row["returns"] else {},
        "public": bool(row["public"]),
    }


def get_replay(replay_id: str, viewer: str | None) -> dict[str, Any] | None:
    """A replay with its full event log — only for participants, or anyone once
    published. Returns None when it doesn't exist *or* the viewer may not see it
    (indistinguishable on purpose)."""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM replays WHERE id = ?", (replay_id,)
        ).fetchone()
        if row is None:
            return None
        view = _replay_row_view(row)
        if not view["public"] and (viewer is None or viewer not in view["seats"]):
            return None
        events = conn.execute(
            "SELECT payload FROM replay_events WHERE replay_id = ? ORDER BY idx",
            (replay_id,),
        ).fetchall()
    view["events"] = [json.loads(e["payload"]) for e in events]
    return view


def list_replays(
    *,
    game_id: str | None = None,
    account_id: str | None = None,
    public_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Replay summaries (no event logs), newest first. `account_id` filters to a
    participant; `public_only` is the public replay browser's view."""
    init_db()
    clauses: list[str] = []
    params: list[Any] = []
    if game_id:
        clauses.append("game_id = ?")
        params.append(game_id)
    if public_only:
        clauses.append("public = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM replays {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit * 4 if account_id else limit),
        ).fetchall()
    views = [_replay_row_view(r) for r in rows]
    if account_id:
        views = [v for v in views if account_id in v["seats"]][:limit]
    return views


def publish_replay(replay_id: str, account_id: str) -> bool:
    """Make a replay public. Only a participant may; returns True on success."""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT seats FROM replays WHERE id = ?", (replay_id,)
        ).fetchone()
        if row is None or account_id not in json.loads(row["seats"]):
            return False
        conn.execute("UPDATE replays SET public = 1 WHERE id = ?", (replay_id,))
    return True


# ---- Plaza: gamified profiles ---------------------------------------------

# Cumulative XP thresholds; your level is the number of thresholds you've passed.
# The curve steepens so early levels come fast (sign-in + a couple of games) and
# later ones are a grind — the usual gamified shape.
LEVEL_THRESHOLDS = (0, 30, 90, 200, 400, 700, 1150, 1800, 2700, 4000, 6000)


def level_for_xp(xp: int) -> int:
    """1-based level for a total XP: level N once you've crossed threshold N-1."""
    level = 1
    for threshold in LEVEL_THRESHOLDS[1:]:
        if xp >= threshold:
            level += 1
        else:
            break
    return level


def _next_threshold(level: int) -> int | None:
    """XP needed to reach the next level (None once past the last threshold)."""
    return LEVEL_THRESHOLDS[level] if level < len(LEVEL_THRESHOLDS) else None


#: Profile text caps. `status_text` is a one-liner under your name — long enough
#: for "afk til 6" and short enough that it can't be a second bio.
BIO_MAX = 280
STATUS_MAX = 80
#: An emoji or two. This is the *fallback* avatar rendered inline in rosters; an
#: uploaded image lives in `avatar_url`. Writing an image here is what used to
#: truncate every upload to 8 bytes of base64.
AVATAR_EMOJI_MAX = 8


def _profile_view(
    account_id: str,
    avatar: str,
    bio: str,
    xp: int,
    *,
    avatar_url: str | None = None,
    background_url: str | None = None,
    background_id: str | None = None,
    status_text: str = "",
    showcase: Any = None,
) -> dict[str, Any]:
    level = level_for_xp(xp)
    return {
        "account_id": account_id,
        "avatar": avatar,
        "avatar_url": avatar_url,
        "background_url": background_url,
        "background_id": background_id,
        "status_text": status_text,
        "showcase": showcase or [],
        "bio": bio,
        "xp": xp,
        "level": level,
        "level_floor": LEVEL_THRESHOLDS[level - 1],
        "next_level_xp": _next_threshold(level),
    }


#: Every column `_profile_view` needs, in one place so the three readers can't drift.
_PROFILE_COLUMNS = (
    "avatar, bio, xp, avatar_url, background_url, background_id, status_text, showcase"
)


def _view_from_row(account_id: str, row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return _profile_view(account_id, "🙂", "", 0)
    try:
        showcase = json.loads(row["showcase"]) if row["showcase"] else []
    except ValueError:
        showcase = []
    return _profile_view(
        account_id,
        row["avatar"],
        row["bio"],
        row["xp"],
        avatar_url=row["avatar_url"],
        background_url=row["background_url"],
        background_id=row["background_id"],
        status_text=row["status_text"] or "",
        showcase=showcase,
    )


def get_profile(account_id: str) -> dict[str, Any]:
    """A player's profile (artwork, bio, status, xp, derived level, handle).
    Returns sane defaults for an account that has never touched the Plaza."""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT {_PROFILE_COLUMNS} FROM player_profiles WHERE account_id = ?",  # noqa: S608 — fixed column list, not user input
            (account_id,),
        ).fetchone()
        account = conn.execute(
            "SELECT handle, display_name FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
    view = _view_from_row(account_id, row)
    view["handle"] = account["handle"] if account else None
    view["display_name"] = account["display_name"] if account else account_id
    return view


def profile_by_handle(handle: str) -> dict[str, Any] | None:
    """Somebody *else's* profile, looked up by callsign.

    The endpoint that did not exist. Without it there was no way to see another
    player's bio or artwork, which is why the Plaza's player card shipped with a
    hardcoded placeholder paragraph standing in for everyone's.

    Returns None for an unknown handle rather than an empty profile — "no such
    player" and "a player who has written nothing" are different answers.
    """
    init_db()
    with get_conn() as conn:
        account = conn.execute(
            "SELECT id, handle, display_name FROM accounts WHERE handle = ?",
            (handle.strip().lower(),),
        ).fetchone()
        if account is None:
            return None
        row = conn.execute(
            f"SELECT {_PROFILE_COLUMNS} FROM player_profiles WHERE account_id = ?",  # noqa: S608 — fixed column list, not user input
            (account["id"],),
        ).fetchone()
    view = _view_from_row(account["id"], row)
    view["handle"] = account["handle"]
    view["display_name"] = account["display_name"]
    return view


#: How many profiles one card lookup may ask about. Same reasoning and the same
#: ceiling as `MAX_PERSON_LOOKUP`: a roster is tens of rows, and this must not
#: become a way to enumerate the player base.
MAX_PROFILE_CARDS = 100


def profile_cards(handles: list[str]) -> dict[str, dict[str, Any]]:
    """The small slice of many profiles a *list* needs, in one query.

    A friends list wants an avatar, a level and a status line per row. Fetching
    each one with `profile_by_handle` is one request per friend, on every render of
    a pane that opens by default — so the roster gets one batched call instead, and
    the full profile stays a separate fetch made when someone actually opens one.

    Unknown handles are simply absent from the result: a friend who has never
    signed in to the game server is a normal thing to have, not an error, and the
    roster renders them from local data.
    """
    init_db()
    wanted = [h.strip().lower() for h in handles if h and h.strip()][:MAX_PROFILE_CARDS]
    if not wanted:
        return {}
    placeholders = ",".join("?" * len(wanted))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT a.id, a.handle, a.display_name, {_PROFILE_COLUMNS}"  # noqa: S608 — fixed column list; handles are bound
            " FROM accounts a LEFT JOIN player_profiles p ON p.account_id = a.id"
            f" WHERE a.handle IN ({placeholders})",
            wanted,
        ).fetchall()
    cards: dict[str, dict[str, Any]] = {}
    for row in rows:
        # A LEFT JOIN miss is a real row with null profile columns, not a null row —
        # so it cannot be handed to `_view_from_row`, which only defaults when the
        # whole row is absent. An account that has never opened the Plaza is the
        # common case here, not an edge one.
        view = _profile_view(
            row["id"],
            row["avatar"] or "🙂",
            row["bio"] or "",
            row["xp"] or 0,
            avatar_url=row["avatar_url"],
            status_text=row["status_text"] or "",
        )
        cards[row["handle"]] = {
            "handle": row["handle"],
            "display_name": row["display_name"],
            "avatar": view["avatar"],
            "avatar_url": view["avatar_url"],
            "status_text": view["status_text"],
            "level": view["level"],
            "xp": view["xp"],
        }
    return cards


def upsert_profile(
    account_id: str,
    *,
    avatar: str | None = None,
    bio: str | None = None,
    avatar_url: str | None = None,
    background_url: str | None = None,
    background_id: str | None = None,
    status_text: str | None = None,
    showcase: Any = None,
) -> dict[str, Any]:
    """Create or patch a profile. XP is only ever earned via `add_xp`.

    Every field is patch-style: `None` leaves it alone. Clearing an image is
    therefore an explicit empty string, not a `None` — otherwise "don't touch the
    background" and "remove the background" would be the same request.
    """
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT {_PROFILE_COLUMNS} FROM player_profiles WHERE account_id = ?",  # noqa: S608 — fixed column list, not user input
            (account_id,),
        ).fetchone()
        cur = _view_from_row(account_id, row)
        new = {
            "avatar": (avatar if avatar is not None else cur["avatar"])[
                :AVATAR_EMOJI_MAX
            ],
            "bio": (bio if bio is not None else cur["bio"])[:BIO_MAX],
            "avatar_url": avatar_url if avatar_url is not None else cur["avatar_url"],
            "background_url": background_url
            if background_url is not None
            else cur["background_url"],
            "background_id": background_id
            if background_id is not None
            else cur["background_id"],
            "status_text": (
                status_text if status_text is not None else cur["status_text"]
            )[:STATUS_MAX],
            "showcase": json.dumps(
                showcase if showcase is not None else cur["showcase"]
            ),
        }
        conn.execute(
            """
            INSERT INTO player_profiles (
                account_id, avatar, bio, xp, updated_at,
                avatar_url, background_url, background_id, status_text, showcase
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                avatar = excluded.avatar, bio = excluded.bio,
                updated_at = excluded.updated_at,
                avatar_url = excluded.avatar_url,
                background_url = excluded.background_url,
                background_id = excluded.background_id,
                status_text = excluded.status_text,
                showcase = excluded.showcase
            """,
            (
                account_id,
                new["avatar"],
                new["bio"],
                cur["xp"],
                time.time(),
                new["avatar_url"] or None,
                new["background_url"] or None,
                new["background_id"] or None,
                new["status_text"],
                new["showcase"],
            ),
        )
    return get_profile(account_id)


def _add_xp(conn: sqlite3.Connection, account_id: str, amount: int) -> None:
    """Grant XP inside an open transaction (used by `record_result`)."""
    conn.execute(
        """
        INSERT INTO player_profiles (account_id, avatar, bio, xp, updated_at)
        VALUES (?, '🙂', '', ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            xp = xp + excluded.xp, updated_at = excluded.updated_at
        """,
        (account_id, amount, time.time()),
    )


def add_xp(account_id: str, amount: int) -> dict[str, Any]:
    """Grant XP and return the updated profile."""
    init_db()
    with get_conn() as conn:
        _add_xp(conn, account_id, amount)
    return get_profile(account_id)


# ---- profile comments ------------------------------------------------------

#: A wall post, not an essay. Long enough for a real message, short enough that the
#: wall stays scannable and one person can't monopolise it.
COMMENT_MAX = 1000
#: How many a wall returns at once.
COMMENT_PAGE = 50


def add_comment(account_id: str, author_id: str, body: str) -> dict[str, Any] | None:
    """Leave a comment on `account_id`'s wall. Returns the stored comment, or None
    if the body was empty or the wall's owner does not exist.

    Deliberately does **not** require the author to be a friend. A profile wall is
    the public part of a profile — gating it on friendship would make it invisible
    exactly where it is useful, on the profile of someone you just played. Abuse is
    handled by the owner (`hide_comment`), which is the same shape Steam uses.
    """
    text = (body or "").strip()[:COMMENT_MAX]
    if not text:
        return None
    init_db()
    comment_id = uuid.uuid4().hex
    now = time.time()
    with get_conn() as conn:
        owner = conn.execute(
            "SELECT id FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if owner is None:
            return None
        conn.execute(
            "INSERT INTO profile_comments (id, account_id, author_id, body, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (comment_id, account_id, author_id, text, now),
        )
    return {
        "id": comment_id,
        "account_id": account_id,
        "author_id": author_id,
        "body": text,
        "created_at": now,
    }


def list_comments(
    account_id: str, limit: int = COMMENT_PAGE, before: float | None = None
) -> list[dict[str, Any]]:
    """A wall, newest first, with each author's current name and avatar joined in.

    Joined rather than denormalised at write time so a comment shows who its author
    *is*, not who they were called when they wrote it — the same reason the roster
    reads names live.
    """
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.account_id, c.author_id, c.body, c.created_at,
                   a.display_name AS author_name, a.handle AS author_handle,
                   COALESCE(p.avatar, '🙂') AS author_avatar,
                   p.avatar_url AS author_avatar_url
            FROM profile_comments c
            LEFT JOIN accounts a ON a.id = c.author_id
            LEFT JOIN player_profiles p ON p.account_id = c.author_id
            WHERE c.account_id = ? AND c.hidden = 0 AND (? IS NULL OR c.created_at < ?)
            ORDER BY c.created_at DESC
            LIMIT ?
            """,
            (account_id, before, before, min(int(limit), COMMENT_PAGE)),
        ).fetchall()
    return [dict(r) for r in rows]


def hide_comment(comment_id: str, actor_id: str) -> bool:
    """Hide a comment. The **wall's owner or the comment's author** may; nobody else.

    Hidden, not deleted: a wall owner moderating their own page should not be able
    to destroy the record of what was said to them, and an author retracting their
    own words is the same operation from the reader's side.
    """
    init_db()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE profile_comments SET hidden = 1 "
            "WHERE id = ? AND (account_id = ? OR author_id = ?)",
            (comment_id, actor_id, actor_id),
        )
        return cur.rowcount > 0


# ---- profile media ---------------------------------------------------------

#: Per-image cap. The server is one small machine with a volume, and a profile
#: picture that needs more than this is not a profile picture.
MEDIA_MAX_BYTES = 256 * 1024
#: Formats we will store. Allowlist, never a blocklist — and no SVG, which is a
#: script-execution surface dressed as an image.
MEDIA_MIME: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
#: How many images one account may keep. Uploading past it evicts their oldest.
MEDIA_PER_ACCOUNT = 12


def media_dir() -> Path:
    path = paths.data_dir() / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def media_path(sha: str) -> Path:
    return media_dir() / sha


def store_media(account_id: str, kind: str, mime: str, data: bytes) -> dict[str, Any]:
    """Persist an uploaded image, content-addressed. Returns `{sha256, url, ...}`
    or `{error}`.

    Content-addressed so re-uploading the same picture costs nothing and so the URL
    is immutable — which is what lets `GET /media/{sha}` be cached forever instead
    of revalidated on every profile view.

    The MIME is taken from the **caller's declaration checked against the bytes**,
    not from the filename: an allowlist keyed on a client-supplied extension is not
    an allowlist.
    """
    if mime not in MEDIA_MIME:
        return {"error": f"unsupported image type {mime!r}"}
    if not data:
        return {"error": "empty upload"}
    if len(data) > MEDIA_MAX_BYTES:
        return {"error": f"image is larger than {MEDIA_MAX_BYTES // 1024} KB"}
    if _sniff_mime(data) != mime:
        return {"error": "file content does not match its declared type"}

    sha = hashlib.sha256(data).hexdigest()
    init_db()
    path = media_path(sha)
    if not path.exists():
        path.write_bytes(data)
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO profile_media (sha256, account_id, kind, mime, bytes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(sha256) DO NOTHING",
            (sha, account_id, kind, mime, len(data), now),
        )
        stale = conn.execute(
            "SELECT sha256 FROM profile_media WHERE account_id = ? "
            "ORDER BY created_at DESC LIMIT -1 OFFSET ?",
            (account_id, MEDIA_PER_ACCOUNT),
        ).fetchall()
        for row in stale:
            conn.execute("DELETE FROM profile_media WHERE sha256 = ?", (row["sha256"],))
            # Only unlink once no row anywhere still references the blob — two
            # accounts uploading the same image share one file.
            still = conn.execute(
                "SELECT 1 FROM profile_media WHERE sha256 = ?", (row["sha256"],)
            ).fetchone()
            if still is None:
                media_path(row["sha256"]).unlink(missing_ok=True)
    return {"sha256": sha, "url": f"/media/{sha}", "mime": mime, "bytes": len(data)}


def get_media(sha: str) -> tuple[bytes, str] | None:
    """Blob + MIME for a stored image, or None. `sha` is validated as hex here
    because it lands in a filesystem path — a `..` would otherwise walk out of the
    media directory."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        return None
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT mime FROM profile_media WHERE sha256 = ?", (sha,)
        ).fetchone()
    if row is None:
        return None
    path = media_path(sha)
    if not path.is_file():
        return None
    return path.read_bytes(), str(row["mime"])


#: Magic-number prefixes, so a declared MIME can be checked against real bytes.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_mime(data: bytes) -> str | None:
    """The image type the bytes actually are, or None."""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    # WebP is RIFF-framed: "RIFF" then 4 size bytes then "WEBP".
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


# ---- Plaza: friendships ----------------------------------------------------


def _pair(a: str, b: str) -> tuple[str, str]:
    """Canonical (account_a, account_b) ordering so each friendship is one row."""
    return (a, b) if a <= b else (b, a)


def request_friend(requester: str, target: str) -> str:
    """Ask to friend `target`. If they'd already asked you, this accepts (mutual
    intent → friends). Returns the resulting status ('pending' or 'accepted')."""
    if requester == target:
        return "self"
    init_db()
    a, b = _pair(requester, target)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, requested_by FROM friendships WHERE account_a = ? AND account_b = ?",
            (a, b),
        ).fetchone()
        if row is not None:
            if row["status"] == "accepted":
                return "accepted"
            # A pending request the *other* side opened → this reply accepts it.
            if row["requested_by"] != requester:
                conn.execute(
                    "UPDATE friendships SET status = 'accepted', updated_at = ? WHERE account_a = ? AND account_b = ?",
                    (time.time(), a, b),
                )
                return "accepted"
            return "pending"
        conn.execute(
            "INSERT INTO friendships (account_a, account_b, status, requested_by, updated_at) VALUES (?, ?, 'pending', ?, ?)",
            (a, b, requester, time.time()),
        )
    return "pending"


def accept_friend(account_id: str, other: str) -> bool:
    """Accept an incoming pending request. Only the addressee (not the requester)
    can accept. Returns True if a pending request became a friendship."""
    init_db()
    a, b = _pair(account_id, other)
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE friendships SET status = 'accepted', updated_at = ?
            WHERE account_a = ? AND account_b = ?
              AND status = 'pending' AND requested_by = ?
            """,
            (time.time(), a, b, other),
        )
        return cur.rowcount > 0


def remove_friend(account_id: str, other: str) -> None:
    """Remove a friend or decline/cancel a pending request (either side may)."""
    init_db()
    a, b = _pair(account_id, other)
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM friendships WHERE account_a = ? AND account_b = ?", (a, b)
        )


def _friend_row_view(conn: sqlite3.Connection, other_id: str) -> dict[str, Any]:
    """One friend, as the roster shows them.

    Carries `handle` and `person_id` alongside the account id because those are the
    two names a *human* has: the callsign the ladder shows, and the fabric person the
    friends roster keys on. Without them the node receives a list of `account_id`s it
    cannot match against its own `social_friends` rows, which is precisely how the
    same person came to occupy two unrelated friend lists. Both are nullable — an
    account that never bound a person key is still a perfectly good ladder friend.
    """
    row = conn.execute(
        """
        SELECT COALESCE(a.display_name, ?) AS display_name,
               a.handle    AS handle,
               a.person_id AS person_id,
               COALESCE(p.avatar, '🙂') AS avatar,
               COALESCE(p.xp, 0) AS xp
        FROM (SELECT ? AS id) x
        LEFT JOIN accounts a ON a.id = x.id
        LEFT JOIN player_profiles p ON p.account_id = x.id
        """,
        (other_id, other_id),
    ).fetchone()
    return {
        "account_id": other_id,
        "display_name": row["display_name"],
        "handle": row["handle"],
        "person_id": row["person_id"],
        "avatar": row["avatar"],
        "level": level_for_xp(row["xp"]),
    }


def list_friends(account_id: str) -> list[dict[str, Any]]:
    """Accepted friends of `account_id` (both directions), with avatar + level."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT CASE WHEN account_a = ? THEN account_b ELSE account_a END AS other
            FROM friendships
            WHERE (account_a = ? OR account_b = ?) AND status = 'accepted'
            """,
            (account_id, account_id, account_id),
        ).fetchall()
        return [_friend_row_view(conn, r["other"]) for r in rows]


def list_pending(account_id: str) -> list[dict[str, Any]]:
    """Incoming pending requests (someone else asked to friend `account_id`)."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT requested_by FROM friendships
            WHERE (account_a = ? OR account_b = ?)
              AND status = 'pending' AND requested_by != ?
            """,
            (account_id, account_id, account_id),
        ).fetchall()
        return [_friend_row_view(conn, r["requested_by"]) for r in rows]
