"""hassault credits, keys, cases and StatTrak, on the game server.

**Credits are earned, never bought, never cashed out, never transferred.** They
come from three places, each paid exactly once by its `ref` (migration
`_m15_hassault_wallet`):

- a server-hosted match (`match:<id>`): a little for playing, more for kills
  and a win, a quarter as much with no human opponent, and no more than
  `DAILY_MATCH_CAP` a UTC day;
- each level reached (`level:<n>`);
- the first win of an ISO week (`week:<yyyy-ww>`), which also drops a case.

They are spent on keys (`KEY_PRICE`), the sink. Opening a case consumes the case
and a key and makes a skin in **one transaction** (`BEGIN IMMEDIATE`, so two
opens of the same case cannot both read it as owned), rolled with this
server's RNG at the odds `cases.py` serves.

The balance is `SUM(delta)`, never a column. A spend runs under the write lock,
and the ledger's trigger refuses any row that would take it below zero.

**StatTrak** counts human kills in server rooms, per match, onto the skin the
killer had equipped for that weapon on that side, through a ledger keyed by
`(match, item)` so a match is counted once.
"""

from __future__ import annotations

import datetime as dt
import random
import sqlite3
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from backend.games_server import store

router = APIRouter(prefix="/api/hassault", tags=["hassault-wallet"])

KEY_PRICE = 250
#: Most credits matches may pay one account in a UTC day.
DAILY_MATCH_CAP = 200
MATCH_BASE = 5
MATCH_PER_KILL = 1
MATCH_WIN = 10
#: A match against bots alone pays this share.
BOT_SHARE = 0.25
LEVEL_REWARD = 50
WEEKLY_WIN_REWARD = 100
#: The case the weekly win drops.
WEEKLY_CASE = "field_case"

#: The server's own RNG for case rolls; a seeded one in tests.
_rng: random.Random = random.SystemRandom()


class InsufficientCredits(Exception):
    pass


def balance(account_id: str, conn: sqlite3.Connection | None = None) -> int:
    if conn is None:
        store.init_db()
        with store.get_conn() as own:
            return balance(account_id, own)
    row = conn.execute(
        "SELECT COALESCE(SUM(delta), 0) AS b FROM hassault_wallet_ledger "
        "WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    return int(row["b"])


def _post(
    conn: sqlite3.Connection,
    account_id: str,
    delta: int,
    kind: str,
    ref: str,
    at: float | None = None,
) -> bool:
    """One ledger row. `False` when this `(kind, ref)` was already paid;
    raises `InsufficientCredits` when the trigger refuses an overdraft."""
    try:
        conn.execute(
            "INSERT INTO hassault_wallet_ledger (account_id, delta, kind, ref, at) "
            "VALUES (?, ?, ?, ?, ?)",
            (account_id, int(delta), kind, ref, time.time() if at is None else at),
        )
    except sqlite3.IntegrityError as exc:
        if "insufficient credits" in str(exc):
            raise InsufficientCredits(str(exc)) from exc
        return False
    return True


# ---- earning --------------------------------------------------------------------


def match_credits(result: dict[str, Any]) -> int:
    """What one server-hosted match pays, before the daily cap."""
    kills = int(result.get("kills") or 0)
    raw = MATCH_BASE + kills * MATCH_PER_KILL + (MATCH_WIN if result.get("won") else 0)
    if int(result.get("humanOpponents") or 0) <= 0:
        raw = int(raw * BOT_SHARE)
    return max(0, raw)


def _today_start(now: float) -> float:
    day = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
    return dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp()


def _week_ref(now: float) -> str:
    year, week, _ = dt.datetime.fromtimestamp(now, dt.timezone.utc).isocalendar()
    return f"{year}-{week:02d}"


def on_match(
    account_id: str,
    match_id: str,
    result: dict[str, Any],
    level_before: int,
    level_after: int,
    now: float | None = None,
) -> dict[str, Any]:
    """Pay what a finished server match earned: the match (capped by the day),
    every level it reached, and the week's first win (with a case)."""
    now = time.time() if now is None else now
    paid: dict[str, Any] = {"match": 0, "levels": 0, "weekly": 0, "case": None}
    if not result.get("recordable", True):
        return paid
    store.init_db()
    with store.get_conn() as conn:
        today = conn.execute(
            "SELECT COALESCE(SUM(delta), 0) AS n FROM hassault_wallet_ledger "
            "WHERE account_id = ? AND kind = 'match' AND at >= ?",
            (account_id, _today_start(now)),
        ).fetchone()["n"]
        amount = min(match_credits(result), max(0, DAILY_MATCH_CAP - int(today)))
        # Stamped with the `now` the cap was judged at, so the next day's
        # cap reads the same clock.
        if amount > 0 and _post(conn, account_id, amount, "match", match_id, now):
            paid["match"] = amount
        for level in range(level_before + 1, level_after + 1):
            if _post(conn, account_id, LEVEL_REWARD, "level", str(level)):
                paid["levels"] += LEVEL_REWARD
        if result.get("won") and _post(
            conn, account_id, WEEKLY_WIN_REWARD, "weekly", _week_ref(now)
        ):
            paid["weekly"] = WEEKLY_WIN_REWARD
            paid["case"] = _grant_case(
                conn, account_id, WEEKLY_CASE, f"week:{_week_ref(now)}"
            )
    return paid


def _grant_case(
    conn: sqlite3.Connection, account_id: str, case_id: str, ref: str
) -> str:
    instance = str(uuid.uuid4())
    _insert_item(conn, instance, account_id, "case", case_id, "reward", ref)
    return instance


def _insert_item(
    conn: sqlite3.Connection,
    instance_id: str,
    account_id: str,
    kind: str,
    def_id: str,
    origin: str,
    ref: str,
    float_value: float = 0.0,
    seed: int = 0,
    stattrak: int | None = None,
) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO hassault_items (instance_id, owner_id, def_id, kind, float_value, "
        "pattern_seed, stattrak, origin, tradable_after, acquired_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            instance_id,
            account_id,
            def_id,
            kind,
            float_value,
            seed,
            stattrak,
            origin,
            now + 7 * 86400,
            now,
        ),
    )
    conn.execute(
        "INSERT INTO hassault_item_events (instance_id, kind, to_id, ref, at) "
        "VALUES (?, ?, ?, ?, ?)",
        (instance_id, origin, account_id, ref, now),
    )


# ---- spending ---------------------------------------------------------------------


def buy_key(account_id: str) -> dict[str, Any]:
    """Spend `KEY_PRICE` on a key. Raises `InsufficientCredits`."""
    from backend.modules.hassault import cases

    store.init_db()
    with store.get_conn() as conn:
        # The write lock before the balance is read: two buys cannot both see
        # the same credits. The trigger is the floor under that.
        conn.execute("BEGIN IMMEDIATE")
        ref = uuid.uuid4().hex
        _post(conn, account_id, -KEY_PRICE, "key", ref)
        instance = str(uuid.uuid4())
        _insert_item(conn, instance, account_id, "key", cases.KEY_ID, "store", ref)
    return {"instanceId": instance, "balance": balance(account_id)}


def open_case(account_id: str, case_instance_id: str) -> dict[str, Any] | None:
    """Consume an owned case and one key, and make the skin they roll.

    `None` when the case is not this account's to open, or there is no key.
    All of it under one write lock: a second open of the same case waits, then
    finds it consumed.
    """
    from backend.games_server.hassault_account import _item_dict
    from backend.modules.hassault import cases

    store.init_db()
    with store.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        case_row = conn.execute(
            "SELECT * FROM hassault_items WHERE instance_id = ? AND owner_id = ? "
            "AND kind = 'case' AND state = 'owned'",
            (case_instance_id, account_id),
        ).fetchone()
        case = cases.CASE_BY_ID.get(str(case_row["def_id"])) if case_row else None
        key_row = conn.execute(
            "SELECT instance_id FROM hassault_items WHERE owner_id = ? AND kind = 'key' "
            "AND state = 'owned' ORDER BY acquired_at LIMIT 1",
            (account_id,),
        ).fetchone()
        if case is None or key_row is None:
            return None
        now = time.time()
        for burnt in (case_instance_id, key_row["instance_id"]):
            conn.execute(
                "UPDATE hassault_items SET state = 'consumed' WHERE instance_id = ?",
                (burnt,),
            )
            conn.execute(
                "INSERT INTO hassault_item_events (instance_id, kind, from_id, ref, at) "
                "VALUES (?, 'opened', ?, ?, ?)",
                (burnt, account_id, case_instance_id, now),
            )
        made = cases.roll(case, _rng)
        _insert_item(
            conn,
            made.instance_id,
            account_id,
            "skin",
            made.skin_id,
            "case",
            f"case:{case_instance_id}",
            made.float_value,
            made.pattern_seed,
            made.stat_tracker_kills,
        )
        row = conn.execute(
            "SELECT * FROM hassault_items WHERE instance_id = ?", (made.instance_id,)
        ).fetchone()
    out = _item_dict(row)
    out["caseId"] = case.id
    return out


def wallet(account_id: str, history: int = 20) -> dict[str, Any]:
    """The balance, the cases and keys owned, and the latest ledger rows."""
    from backend.modules.hassault import cases

    store.init_db()
    with store.get_conn() as conn:
        owned = conn.execute(
            "SELECT instance_id, kind, def_id, acquired_at FROM hassault_items "
            "WHERE owner_id = ? AND state = 'owned' AND kind IN ('case', 'key') "
            "ORDER BY acquired_at",
            (account_id,),
        ).fetchall()
        rows = conn.execute(
            "SELECT delta, kind, ref, at FROM hassault_wallet_ledger WHERE account_id = ? "
            "ORDER BY entry_id DESC LIMIT ?",
            (account_id, max(1, min(100, history))),
        ).fetchall()
        total = balance(account_id, conn)
    return {
        "balance": total,
        "keyPrice": KEY_PRICE,
        "cases": [
            {
                "instanceId": r["instance_id"],
                "caseId": r["def_id"],
                "name": getattr(cases.CASE_BY_ID.get(r["def_id"]), "name", r["def_id"]),
            }
            for r in owned
            if r["kind"] == "case"
        ],
        "keys": sum(1 for r in owned if r["kind"] == "key"),
        "history": [dict(r) for r in rows],
    }


# ---- StatTrak ---------------------------------------------------------------------


def count_stattrak(
    account_id: str, match_id: str, kills: dict[tuple[str, str], int]
) -> int:
    """Add a match's human kills to the StatTrak skins that made them.

    `kills` is `{(side, weapon id): count}`. Each lands on the item equipped for
    that weapon on that side, if it carries a counter; the ledger row goes in
    first, so a match is counted once per item. Returns kills counted.
    """
    counted = 0
    store.init_db()
    with store.get_conn() as conn:
        for (side, weapon_id), n in kills.items():
            if n <= 0:
                continue
            row = conn.execute(
                "SELECT i.instance_id FROM hassault_equips e JOIN hassault_items i "
                "ON i.instance_id = e.instance_id WHERE e.account_id = ? AND e.side = ? "
                "AND e.weapon_id = ? AND i.owner_id = ? AND i.state = 'owned' "
                "AND i.stattrak IS NOT NULL",
                (account_id, side, weapon_id, account_id),
            ).fetchone()
            if row is None:
                continue
            try:
                conn.execute(
                    "INSERT INTO hassault_stattrak_ledger VALUES (?, ?, ?, ?)",
                    (match_id, row["instance_id"], int(n), time.time()),
                )
            except sqlite3.IntegrityError:
                continue
            conn.execute(
                "UPDATE hassault_items SET stattrak = stattrak + ? WHERE instance_id = ?",
                (int(n), row["instance_id"]),
            )
            counted += int(n)
    return counted


# ---- routes -----------------------------------------------------------------------


def _account(authorization: str | None) -> str:
    from backend.games_server.hassault_account import _account as resolve

    return resolve(authorization)


@router.get("/account/wallet")
def get_wallet(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return wallet(_account(authorization))


@router.post("/account/keys/buy")
def post_buy_key(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    account_id = _account(authorization)
    try:
        return buy_key(account_id)
    except InsufficientCredits as exc:
        raise HTTPException(
            status_code=409, detail=f"a key costs {KEY_PRICE} credits"
        ) from exc


@router.post("/account/cases/open")
def post_open_case(
    instance_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    made = open_case(_account(authorization), instance_id)
    if made is None:
        raise HTTPException(
            status_code=409, detail="you need that case and a key to open it"
        )
    return made
