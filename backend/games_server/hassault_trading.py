"""hassault trading and the market, on the game server.

The first place an item leaves the account that earned it, so most of this file
is reasons to refuse. Each rule is stated where it is enforced:

- **Who may trade** (`eligibility`): a real account (not a guest, not a bot) at
  least `MIN_ACCOUNT_AGE` old, at level `MIN_LEVEL`, with `MIN_SERVER_MATCHES`
  server-hosted matches behind it. A fresh account farming drops to pass on is
  the abuse this exists to make slow.
- **What may move** (`_movable`): owned, in state `owned` (so not listed, not
  consumed), past its `tradable_after` hold. Imports have no hold date and so
  never move; drops, case openings and trades set one a week out.
- **To whom**: a friend (`friendships`, accepted) or anyone holding your trade
  link token.
- **How**: offer (immutable from then on), the recipient accepts, which arms a
  confirm `ARMING_DELAY` seconds later, and the recipient confirms. The swap
  re-checks every item and both accounts under `BEGIN IMMEDIATE`; anything that
  changed since the offer fails the trade and moves nothing. Moved items lose
  their equips and get a fresh hold.
- **The market** sells for credits only. A purchase debits the buyer the price
  and credits the seller the price less `MARKET_FEE` (at least `MIN_FEE`), and
  the fee is burnt: no account receives it, and `hassault_sinks` records it.
- **Rate limits**: `OFFERS_PER_DAY`, `PENDING_PER_PAIR`, `MAX_LISTINGS`,
  `PURCHASES_PER_DAY`.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.games_server import store

router = APIRouter(prefix="/api/hassault", tags=["hassault-trading"])

MIN_ACCOUNT_AGE = 7 * 86400
MIN_LEVEL = 5
MIN_SERVER_MATCHES = 10
#: How long after a trade or a purchase an item may move again.
TRADE_HOLD = 7 * 86400
ARMING_DELAY = 10.0
#: An offer nobody answered lapses.
OFFER_TTL = 7 * 86400
MAX_ITEMS_PER_SIDE = 16

OFFERS_PER_DAY = 30
PENDING_PER_PAIR = 5
MAX_LISTINGS = 50
PURCHASES_PER_DAY = 100

MARKET_FEE = 0.10
MIN_FEE = 1
MIN_PRICE = 10
MAX_PRICE = 100_000

#: The item kinds that may move at all.
MOVABLE_KINDS = ("skin", "case", "key")


class Refused(Exception):
    """A trade or market action refused, with a reason to show the player."""


# ---- who may trade ------------------------------------------------------------------


def eligibility(account_id: str, now: float | None = None) -> dict[str, Any]:
    """Whether `account_id` may trade, and every reason it may not."""
    from backend.games_server import hassault_account

    now = time.time() if now is None else now
    store.init_db()
    reasons: list[str] = []
    with store.get_conn() as conn:
        row = conn.execute(
            "SELECT created_at, is_bot FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        matches = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM hassault_xp_ledger "
                "WHERE account_id = ? AND source = 'match'",
                (account_id,),
            ).fetchone()["n"]
        )
    if account_id.startswith("guest_") or row is None:
        reasons.append("sign in with an account to trade")
    elif int(row["is_bot"] or 0):
        reasons.append("bots do not trade")
    elif now - float(row["created_at"]) < MIN_ACCOUNT_AGE:
        reasons.append(f"accounts trade from {MIN_ACCOUNT_AGE // 86400} days old")
    level = hassault_account.progression(account_id)["level"]
    if level < MIN_LEVEL:
        reasons.append(f"trading opens at level {MIN_LEVEL} (you are {level})")
    if matches < MIN_SERVER_MATCHES:
        reasons.append(
            f"play {MIN_SERVER_MATCHES} server matches first ({matches} so far)"
        )
    return {
        "accountId": account_id,
        "eligible": not reasons,
        "reasons": reasons,
        "level": level,
        "serverMatches": matches,
    }


def _require_eligible(account_id: str) -> None:
    status = eligibility(account_id)
    if not status["eligible"]:
        raise Refused(status["reasons"][0])


def _are_friends(conn: sqlite3.Connection, a: str, b: str) -> bool:
    x, y = store._pair(a, b)
    row = conn.execute(
        "SELECT 1 FROM friendships WHERE account_a = ? AND account_b = ? "
        "AND status = 'accepted'",
        (x, y),
    ).fetchone()
    return row is not None


def trade_link(account_id: str, rotate: bool = False) -> str:
    """This account's trade link token: holding it lets a non-friend offer.
    Rotating it revokes every copy handed out before."""
    store.init_db()
    with store.get_conn() as conn:
        row = conn.execute(
            "SELECT token FROM hassault_trade_links WHERE account_id = ?", (account_id,)
        ).fetchone()
        if row is not None and not rotate:
            return str(row["token"])
        token = secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO hassault_trade_links VALUES (?, ?) ON CONFLICT(account_id) "
            "DO UPDATE SET token = excluded.token",
            (account_id, token),
        )
    return token


# ---- what may move ------------------------------------------------------------------


def _movable(
    conn: sqlite3.Connection, owner: str, instance_ids: list[str], now: float
) -> list[sqlite3.Row]:
    """Every item, if all of them are `owner`'s to move now; else `Refused`."""
    if len(set(instance_ids)) != len(instance_ids):
        raise Refused("an item is named twice")
    rows = []
    for instance in instance_ids:
        row = conn.execute(
            "SELECT * FROM hassault_items WHERE instance_id = ?", (instance,)
        ).fetchone()
        if row is None or row["owner_id"] != owner:
            raise Refused("an item in this trade is not theirs to give")
        if row["state"] != "owned":
            raise Refused("an item in this trade is listed or gone")
        if row["kind"] not in MOVABLE_KINDS:
            raise Refused("that kind of item does not trade")
        hold = row["tradable_after"]
        if hold is None:
            raise Refused("imported items never trade")
        if float(hold) > now:
            raise Refused("an item in this trade is still on its trade hold")
        rows.append(row)
    return rows


def _move(
    conn: sqlite3.Connection, instance_id: str, src: str, dst: str, ref: str, now: float
) -> None:
    """Hand one item over: new owner, a fresh hold, no equips, an event."""
    conn.execute(
        "UPDATE hassault_items SET owner_id = ?, state = 'owned', equipped = 0, "
        "tradable_after = ? WHERE instance_id = ?",
        (dst, now + TRADE_HOLD, instance_id),
    )
    conn.execute("DELETE FROM hassault_equips WHERE instance_id = ?", (instance_id,))
    conn.execute(
        "INSERT INTO hassault_item_events (instance_id, kind, from_id, to_id, ref, at) "
        "VALUES (?, 'trade', ?, ?, ?, ?)",
        (instance_id, src, dst, ref, now),
    )


def partner_items(
    viewer: str, partner: str, token: str = "", now: float | None = None
) -> list[dict[str, Any]]:
    """What `partner` could trade right now, for a viewer allowed to offer them
    anything: a friend, or a holder of their trade link. Nobody else sees
    another player's inventory through here."""
    from backend.games_server.hassault_account import _item_dict

    now = time.time() if now is None else now
    store.init_db()
    with store.get_conn() as conn:
        link = conn.execute(
            "SELECT token FROM hassault_trade_links WHERE account_id = ?", (partner,)
        ).fetchone()
        by_link = bool(token) and link is not None and secrets.compare_digest(
            str(link["token"]), token
        )
        if viewer != partner and not by_link and not _are_friends(conn, viewer, partner):
            raise Refused("you can see a player's items once you are friends")
        rows = conn.execute(
            "SELECT * FROM hassault_items WHERE owner_id = ? AND state = 'owned' "
            "AND kind IN ('skin', 'case', 'key') AND tradable_after IS NOT NULL "
            "AND tradable_after <= ? ORDER BY acquired_at",
            (partner, now),
        ).fetchall()
    out = []
    for r in rows:
        if r["kind"] == "skin":
            out.append({**_item_dict(r), "kind": "skin"})
        else:
            out.append({"instanceId": r["instance_id"], "kind": r["kind"], "defId": r["def_id"]})
    return out


# ---- trades -------------------------------------------------------------------------


def _trade_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "tradeId": row["trade_id"],
        "from": row["from_id"],
        "to": row["to_id"],
        "give": json.loads(row["give"]),
        "want": json.loads(row["want"]),
        "state": row["state"],
        "reason": row["reason"],
        "createdAt": row["created_at"],
        "armsAt": row["arms_at"],
        "doneAt": row["done_at"],
    }


def offer(
    from_id: str,
    to_id: str,
    give: list[str],
    want: list[str],
    token: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Propose a trade. Fixed from here: it can be accepted, declined or
    cancelled, never edited, so what the recipient reviews is what moves."""
    now = time.time() if now is None else now
    if from_id == to_id:
        raise Refused("you cannot trade with yourself")
    if not give and not want:
        raise Refused("a trade needs at least one item")
    if len(give) > MAX_ITEMS_PER_SIDE or len(want) > MAX_ITEMS_PER_SIDE:
        raise Refused(f"at most {MAX_ITEMS_PER_SIDE} items a side")
    _require_eligible(from_id)
    _require_eligible(to_id)
    store.init_db()
    with store.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        link = conn.execute(
            "SELECT token FROM hassault_trade_links WHERE account_id = ?", (to_id,)
        ).fetchone()
        by_link = (
            bool(token)
            and link is not None
            and secrets.compare_digest(str(link["token"]), token)
        )
        if not by_link and not _are_friends(conn, from_id, to_id):
            raise Refused("offers go to friends, or through their trade link")
        sent_today = conn.execute(
            "SELECT COUNT(*) AS n FROM hassault_trades WHERE from_id = ? AND created_at > ?",
            (from_id, now - 86400),
        ).fetchone()["n"]
        if int(sent_today) >= OFFERS_PER_DAY:
            raise Refused(f"at most {OFFERS_PER_DAY} offers a day")
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM hassault_trades WHERE state IN ('offered', 'accepted') "
            "AND ((from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?))",
            (from_id, to_id, to_id, from_id),
        ).fetchone()["n"]
        if int(pending) >= PENDING_PER_PAIR:
            raise Refused(f"at most {PENDING_PER_PAIR} open offers between two players")
        _movable(conn, from_id, give, now)
        _movable(conn, to_id, want, now)
        trade_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO hassault_trades (trade_id, from_id, to_id, give, want, state, "
            "created_at) VALUES (?, ?, ?, ?, ?, 'offered', ?)",
            (trade_id, from_id, to_id, json.dumps(give), json.dumps(want), now),
        )
        row = conn.execute(
            "SELECT * FROM hassault_trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()
    return _trade_dict(row)


def _load(conn: sqlite3.Connection, trade_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM hassault_trades WHERE trade_id = ?", (trade_id,)
    ).fetchone()
    if row is None:
        raise Refused("no such trade")
    return row


def accept(account_id: str, trade_id: str, now: float | None = None) -> dict[str, Any]:
    """The recipient agrees, which arms the confirm `ARMING_DELAY` from now."""
    now = time.time() if now is None else now
    store.init_db()
    with store.get_conn() as conn:
        row = _load(conn, trade_id)
        if row["to_id"] != account_id:
            raise Refused("only the recipient accepts a trade")
        if row["state"] != "offered":
            raise Refused(f"this trade is {row['state']}")
        if now - float(row["created_at"]) > OFFER_TTL:
            conn.execute(
                "UPDATE hassault_trades SET state = 'expired' WHERE trade_id = ?",
                (trade_id,),
            )
            raise Refused("this offer has expired")
        conn.execute(
            "UPDATE hassault_trades SET state = 'accepted', arms_at = ? WHERE trade_id = ?",
            (now + ARMING_DELAY, trade_id),
        )
        row = _load(conn, trade_id)
    return _trade_dict(row)


def confirm(account_id: str, trade_id: str, now: float | None = None) -> dict[str, Any]:
    """Execute an accepted trade, once it has armed. All or nothing: every item
    is re-checked under the write lock, and anything that changed fails the
    trade without moving a single one."""
    now = time.time() if now is None else now
    _require_eligible(account_id)
    store.init_db()
    with store.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _load(conn, trade_id)
        if row["to_id"] != account_id:
            raise Refused("only the recipient confirms a trade")
        if row["state"] != "accepted":
            raise Refused(f"this trade is {row['state']}")
        if now < float(row["arms_at"] or 0):
            raise Refused(f"confirm arms in {float(row['arms_at']) - now:.0f}s")
        give, want = json.loads(row["give"]), json.loads(row["want"])
        try:
            _require_eligible(row["from_id"])
            _movable(conn, row["from_id"], give, now)
            _movable(conn, row["to_id"], want, now)
        except Refused as exc:
            conn.execute(
                "UPDATE hassault_trades SET state = 'failed', reason = ?, done_at = ? "
                "WHERE trade_id = ?",
                (str(exc), now, trade_id),
            )
            conn.commit()
            raise
        for instance in give:
            _move(
                conn, instance, row["from_id"], row["to_id"], f"trade:{trade_id}", now
            )
        for instance in want:
            _move(
                conn, instance, row["to_id"], row["from_id"], f"trade:{trade_id}", now
            )
        conn.execute(
            "UPDATE hassault_trades SET state = 'done', done_at = ? WHERE trade_id = ?",
            (now, trade_id),
        )
        row = _load(conn, trade_id)
    return _trade_dict(row)


def close(account_id: str, trade_id: str, how: str) -> dict[str, Any]:
    """Decline (the recipient) or cancel (the sender) an open trade."""
    store.init_db()
    with store.get_conn() as conn:
        row = _load(conn, trade_id)
        party = row["to_id"] if how == "declined" else row["from_id"]
        if account_id != party:
            raise Refused("that is the other player's to do")
        if row["state"] not in ("offered", "accepted"):
            raise Refused(f"this trade is {row['state']}")
        conn.execute(
            "UPDATE hassault_trades SET state = ?, done_at = ? WHERE trade_id = ?",
            (how, time.time(), trade_id),
        )
        row = _load(conn, trade_id)
    return _trade_dict(row)


def trades_for(account_id: str, limit: int = 50) -> list[dict[str, Any]]:
    store.init_db()
    with store.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM hassault_trades WHERE from_id = ? OR to_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (account_id, account_id, max(1, min(200, limit))),
        ).fetchall()
    return [_trade_dict(r) for r in rows]


# ---- the market ---------------------------------------------------------------------


def fee_for(price: int) -> int:
    return max(MIN_FEE, int(price * MARKET_FEE))


def list_item(account_id: str, instance_id: str, price: int) -> dict[str, Any]:
    """Offer an item for credits. It becomes `listed` until sold or cancelled."""
    if not MIN_PRICE <= int(price) <= MAX_PRICE:
        raise Refused(f"prices run from {MIN_PRICE} to {MAX_PRICE} credits")
    _require_eligible(account_id)
    now = time.time()
    store.init_db()
    with store.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM hassault_listings WHERE seller_id = ? AND state = 'active'",
            (account_id,),
        ).fetchone()["n"]
        if int(active) >= MAX_LISTINGS:
            raise Refused(f"at most {MAX_LISTINGS} listings at once")
        (item,) = _movable(conn, account_id, [instance_id], now)
        listing_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO hassault_listings (listing_id, instance_id, def_id, seller_id, "
            "price, state, created_at) VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (listing_id, instance_id, item["def_id"], account_id, int(price), now),
        )
        conn.execute(
            "UPDATE hassault_items SET state = 'listed', equipped = 0 WHERE instance_id = ?",
            (instance_id,),
        )
        conn.execute(
            "DELETE FROM hassault_equips WHERE instance_id = ?", (instance_id,)
        )
    return {"listingId": listing_id, "price": int(price), "fee": fee_for(int(price))}


def cancel_listing(account_id: str, listing_id: str) -> bool:
    store.init_db()
    with store.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM hassault_listings WHERE listing_id = ? AND seller_id = ? "
            "AND state = 'active'",
            (listing_id, account_id),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE hassault_listings SET state = 'cancelled', closed_at = ? WHERE listing_id = ?",
            (time.time(), listing_id),
        )
        conn.execute(
            "UPDATE hassault_items SET state = 'owned' WHERE instance_id = ?",
            (row["instance_id"],),
        )
    return True


def buy(account_id: str, listing_id: str, now: float | None = None) -> dict[str, Any]:
    """Buy a listing: the buyer pays the price, the seller gets it less the
    fee, the fee is burnt, the item moves. One transaction, under the lock."""
    from backend.games_server import hassault_wallet

    now = time.time() if now is None else now
    _require_eligible(account_id)
    store.init_db()
    with store.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM hassault_listings WHERE listing_id = ? AND state = 'active'",
            (listing_id,),
        ).fetchone()
        if row is None:
            raise Refused("that listing is gone")
        if row["seller_id"] == account_id:
            raise Refused("you cannot buy your own listing")
        bought_today = conn.execute(
            "SELECT COUNT(*) AS n FROM hassault_listings WHERE buyer_id = ? AND closed_at > ?",
            (account_id, now - 86400),
        ).fetchone()["n"]
        if int(bought_today) >= PURCHASES_PER_DAY:
            raise Refused(f"at most {PURCHASES_PER_DAY} purchases a day")
        item = conn.execute(
            "SELECT * FROM hassault_items WHERE instance_id = ?", (row["instance_id"],)
        ).fetchone()
        if (
            item is None
            or item["owner_id"] != row["seller_id"]
            or item["state"] != "listed"
        ):
            raise Refused("that listing is no longer valid")
        price = int(row["price"])
        fee = fee_for(price)
        try:
            hassault_wallet._post(
                conn, account_id, -price, "market_buy", listing_id, now
            )
        except hassault_wallet.InsufficientCredits as exc:
            raise Refused(f"that costs {price} credits") from exc
        hassault_wallet._post(
            conn, row["seller_id"], price - fee, "market_sale", listing_id, now
        )
        conn.execute(
            "INSERT INTO hassault_sinks (kind, ref, amount, at) VALUES ('market_fee', ?, ?, ?)",
            (listing_id, fee, now),
        )
        _move(
            conn,
            row["instance_id"],
            row["seller_id"],
            account_id,
            f"market:{listing_id}",
            now,
        )
        conn.execute(
            "UPDATE hassault_listings SET state = 'sold', buyer_id = ?, closed_at = ? "
            "WHERE listing_id = ?",
            (account_id, now, listing_id),
        )
    return {
        "listingId": listing_id,
        "instanceId": row["instance_id"],
        "price": price,
        "fee": fee,
    }


def listings(def_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Active listings, cheapest first, with each item's own shape."""
    from backend.games_server.hassault_account import _item_dict

    store.init_db()
    with store.get_conn() as conn:
        sql = (
            "SELECT l.listing_id, l.price, l.seller_id, l.created_at, i.* FROM hassault_listings l "
            "JOIN hassault_items i ON i.instance_id = l.instance_id WHERE l.state = 'active'"
        )
        args: list[Any] = []
        if def_id:
            sql += " AND l.def_id = ?"
            args.append(def_id)
        sql += " ORDER BY l.price ASC LIMIT ?"
        args.append(max(1, min(200, limit)))
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        entry = {
            "listingId": r["listing_id"],
            "price": int(r["price"]),
            "sellerId": r["seller_id"],
            "listedAt": r["created_at"],
            "kind": r["kind"],
        }
        if r["kind"] == "skin":
            entry["item"] = _item_dict(r)
        else:
            entry["item"] = {"instanceId": r["instance_id"], "defId": r["def_id"]}
        out.append(entry)
    return out


def price_history(def_id: str, limit: int = 100) -> list[dict[str, Any]]:
    store.init_db()
    with store.get_conn() as conn:
        rows = conn.execute(
            "SELECT price, closed_at FROM hassault_listings WHERE def_id = ? AND state = 'sold' "
            "ORDER BY closed_at DESC LIMIT ?",
            (def_id, max(1, min(500, limit))),
        ).fetchall()
    return [{"price": int(r["price"]), "at": r["closed_at"]} for r in rows]


# ---- routes -------------------------------------------------------------------------


def _account(authorization: str | None) -> str:
    from backend.games_server.hassault_account import _account as resolve

    return resolve(authorization)


def _refusing(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Refused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class OfferBody(BaseModel):
    to: str
    give: list[str] = []
    want: list[str] = []
    token: str = ""


class ListBody(BaseModel):
    instanceId: str  # noqa: N815
    price: int


@router.get("/account/trade/eligibility")
def get_eligibility(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return eligibility(_account(authorization))


@router.get("/account/trade/link")
def get_link(
    rotate: bool = False, authorization: str | None = Header(default=None)
) -> dict[str, str]:
    return {"token": trade_link(_account(authorization), rotate)}


@router.get("/account/trade/partner/{partner_id}")
def get_partner_items(
    partner_id: str, token: str = "", authorization: str | None = Header(default=None)
) -> list[dict[str, Any]]:
    return _refusing(partner_items, _account(authorization), partner_id, token)


@router.get("/account/trades")
def get_trades(
    authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    return trades_for(_account(authorization))


@router.post("/account/trades")
def post_offer(
    body: OfferBody, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    return _refusing(
        offer, _account(authorization), body.to, body.give, body.want, body.token
    )


@router.post("/account/trades/{trade_id}/accept")
def post_accept(
    trade_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    return _refusing(accept, _account(authorization), trade_id)


@router.post("/account/trades/{trade_id}/confirm")
def post_confirm(
    trade_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    return _refusing(confirm, _account(authorization), trade_id)


@router.post("/account/trades/{trade_id}/decline")
def post_decline(
    trade_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    return _refusing(close, _account(authorization), trade_id, "declined")


@router.post("/account/trades/{trade_id}/cancel")
def post_cancel(
    trade_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    return _refusing(close, _account(authorization), trade_id, "cancelled")


@router.get("/market")
def get_market(skin_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return listings(skin_id, limit)


@router.get("/market/history")
def get_history(skin_id: str, limit: int = 100) -> list[dict[str, Any]]:
    return price_history(skin_id, limit)


@router.post("/account/market/list")
def post_list(
    body: ListBody, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    return _refusing(list_item, _account(authorization), body.instanceId, body.price)


@router.post("/account/market/{listing_id}/cancel")
def post_cancel_listing(
    listing_id: str, authorization: str | None = Header(default=None)
) -> dict[str, bool]:
    return {"ok": cancel_listing(_account(authorization), listing_id)}


@router.post("/account/market/{listing_id}/buy")
def post_buy(
    listing_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    return _refusing(buy, _account(authorization), listing_id)
