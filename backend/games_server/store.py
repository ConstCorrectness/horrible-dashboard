"""Persistent accounts + the ELO ladder for the game server.

SQLite at `$HORRIBLE_DATA_DIR/game_server.db` (separate from a node's own data). Three
tables: **accounts** (who played), **ratings** (per-account, per-game ELO + W/L/D), and
**results** (a log of finished games). The referee calls `record_result` on `game_over`;
the leaderboard reads `ratings`.

Ratings are 2-player zero-sum ELO for now (tic-tac-toe/chess/etc.); multi-player games
(poker) will need a pairwise or placement-based extension.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

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


def get_db_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "game_server.db"


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


MIGRATIONS: list[Any] = [
    _m1_identity_and_series,
    _m2_replays,
    _m3_ladder,
    _m4_unique_handles,
    _m5_task_bank,
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


def _profile_view(account_id: str, avatar: str, bio: str, xp: int) -> dict[str, Any]:
    level = level_for_xp(xp)
    return {
        "account_id": account_id,
        "avatar": avatar,
        "bio": bio,
        "xp": xp,
        "level": level,
        "level_floor": LEVEL_THRESHOLDS[level - 1],
        "next_level_xp": _next_threshold(level),
    }


def get_profile(account_id: str) -> dict[str, Any]:
    """A player's gamified profile (avatar, bio, xp, derived level, handle).
    Returns sane defaults for an account that has never touched the Plaza."""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT avatar, bio, xp FROM player_profiles WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        account = conn.execute(
            "SELECT handle FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
    view = (
        _profile_view(account_id, "🙂", "", 0)
        if row is None
        else _profile_view(account_id, row["avatar"], row["bio"], row["xp"])
    )
    view["handle"] = account["handle"] if account else None
    return view


def upsert_profile(
    account_id: str, *, avatar: str | None = None, bio: str | None = None
) -> dict[str, Any]:
    """Create or patch a profile's avatar/bio (XP is only earned via `add_xp`)."""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT avatar, bio, xp FROM player_profiles WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        cur_avatar = row["avatar"] if row else "🙂"
        cur_bio = row["bio"] if row else ""
        cur_xp = row["xp"] if row else 0
        new_avatar = avatar if avatar is not None else cur_avatar
        new_bio = bio if bio is not None else cur_bio
        conn.execute(
            """
            INSERT INTO player_profiles (account_id, avatar, bio, xp, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                avatar = excluded.avatar, bio = excluded.bio,
                updated_at = excluded.updated_at
            """,
            (account_id, new_avatar, new_bio, cur_xp, time.time()),
        )
    return _profile_view(account_id, new_avatar, new_bio, cur_xp)


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
    row = conn.execute(
        """
        SELECT COALESCE(a.display_name, ?) AS display_name,
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
