"""hassault account data on the game server: settings, inventory, XP, drops, import.

Until this, a node's own `app.db` held all of it, so an account's skins and level
were whatever the machine you sat at had. The server is the one place every
machine signed in to an account agrees on, and the standalone web client, which
has no node, can reach it at all.

### The rules, each where it bites

- **The account is the bearer token's, never a field.** Every route resolves the
  caller through `auth.resolve_token`; no body or query names an account.
- **Ledgers, not counters** (migration `_m12_hassault_accounts`). Level is a
  `SUM(xp)` over `hassault_xp_ledger`; a drop is spent by inserting the claim row
  for the level *before* the item, so two racing claims both pick the same level
  and exactly one INSERT survives.
- **XP is the server's word.** Server-hosted matches write ledger rows
  (`grant_match_xp`, from `hassault_rooms`). A node's history arrives once, by
  import, and is marked as such.
- **An import is self-reported, so it is kept apart.** `import_node` inserts the
  `(account, node)` row first, so a second import from the same machine is a
  no-op. Imported items are `origin = 'import'` and never tradable. Imported XP
  counts toward the level shown, but the levels it reaches are pre-claimed: no
  drop is ever paid for a number a node wrote down about itself.
- **Item dicts are the node's own shape** (`SkinInstance.to_dict`), so a client
  reading the inventory cannot tell which backend answered. One field is added:
  `equippedSides`, the sides an item is on (migration `_m13_hassault_equips`).
- **What a player looks like is read here, never sent.** `cosmetics` builds the
  bundle (`cosmetics.py`) from the account's equips and its `hassault.agents`
  setting, and `loadouts` reads `hassault.loadout`; `hassault_rooms` asks both at
  join, and again whenever an equip or one of those settings changes
  (`refresh_account`).

Everything is under `/api/hassault/account/*`, which a node proxies its own
`/skins/*` routes to when it is signed in and the server advertises the
`hassault_account` cap.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.games_server import auth, store

router = APIRouter(prefix="/api/hassault", tags=["hassault-account"])

#: Most items an import may carry. A real inventory is tens; this bounds a node
#: that sends nonsense, not a player.
MAX_IMPORT_ITEMS = 2000
MAX_IMPORT_MATCHES = 20000


def _account(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="sign in required")
    resolved = auth.resolve_token(authorization[7:].strip())
    if not resolved:
        raise HTTPException(status_code=401, detail="sign in required")
    return str(resolved["account_id"])


# ---- settings ---------------------------------------------------------------------


def _account_keys() -> set[str]:
    from backend.modules.hassault import settings_schema

    return {spec.key for spec in settings_schema.SCHEMA if spec.scope == "account"}


def settings_get(account_id: str) -> dict[str, Any]:
    store.init_db()
    with store.get_conn() as conn:
        row = conn.execute(
            "SELECT doc, updated_at FROM hassault_settings WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    if row is None:
        return {"values": {}, "updatedAt": None}
    return {"values": json.loads(row["doc"]), "updatedAt": float(row["updated_at"])}


def settings_put(account_id: str, values: dict[str, Any]) -> dict[str, Any]:
    """Merge `values` into the account's settings. Machine-scoped keys (a GPU, a
    monitor, a binary path) and unknown keys are dropped: they describe one
    computer, and syncing them would put one machine's GPU on another."""
    allowed = _account_keys()
    clean = {k: v for k, v in values.items() if k in allowed}
    current = settings_get(account_id)["values"]
    current.update(clean)
    now = time.time()
    with store.get_conn() as conn:
        conn.execute(
            "INSERT INTO hassault_settings (account_id, doc, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET doc = excluded.doc, "
            "updated_at = excluded.updated_at",
            (account_id, json.dumps(current), now),
        )
    return {
        "values": current,
        "updatedAt": now,
        "dropped": sorted(set(values) - allowed),
    }


# ---- XP -----------------------------------------------------------------------------


def _xp_per_level() -> int:
    from backend.modules.hassault import results

    return int(results.XP_PER_LEVEL)


def _total_xp(
    conn: sqlite3.Connection, account_id: str, source: str | None = None
) -> int:
    sql = "SELECT COALESCE(SUM(xp), 0) AS total FROM hassault_xp_ledger WHERE account_id = ?"
    args: tuple[Any, ...] = (account_id,)
    if source is not None:
        sql += " AND source = ?"
        args = (account_id, source)
    return int(conn.execute(sql, args).fetchone()["total"])


def _level_of(total: int) -> int:
    return 1 + total // _xp_per_level()


def grant_match_xp(account_id: str, match_id: str, result: dict[str, Any]) -> int:
    """Ledger the XP a server-hosted match earned. Idempotent on `match_id`."""
    from backend.modules.hassault import results

    xp = int(results.xp_for(result))
    store.init_db()
    with store.get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO hassault_xp_ledger VALUES (?, 'match', ?, ?, ?)",
            (account_id, match_id, xp, time.time()),
        )
    return xp


def progression(account_id: str) -> dict[str, Any]:
    """Level and drops, every number derived from the ledgers."""
    store.init_db()
    per = _xp_per_level()
    with store.get_conn() as conn:
        total = _total_xp(conn, account_id)
        claimed = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM hassault_drop_claims WHERE account_id = ?",
                (account_id,),
            ).fetchone()["n"]
        )
    level = _level_of(total)
    earned = max(0, level - 1)
    return {
        "level": level,
        "totalXp": total,
        "levelProgressPercent": int((total % per) * 100 / per),
        "dropsEarned": earned,
        "dropsClaimed": claimed,
        "available": max(0, earned - claimed),
        "xpPerLevel": per,
        "xpToNextDrop": per - (total % per),
    }


# ---- inventory ----------------------------------------------------------------------


def _item_dict(row: sqlite3.Row, sides: list[str] | None = None) -> dict[str, Any]:
    """The node's `SkinInstance.to_dict` shape, from a row, plus the sides it
    is equipped on."""
    from backend.modules.hassault.skins import SkinInstance, skin_dict

    instance = SkinInstance(
        instance_id=str(row["instance_id"]),
        skin_id=str(row["def_id"]),
        float_value=float(row["float_value"]),
        pattern_seed=int(row["pattern_seed"]),
        acquired_at=float(row["acquired_at"]),
        is_equipped=bool(row["equipped"]),
        is_tradable=row["tradable_after"] is not None,
        stat_tracker_kills=row["stattrak"],
    )
    out = instance.to_dict(skin_dict().get(instance.skin_id))
    out["equippedSides"] = sorted(sides or [])
    return out


def _sides_by_item(conn: sqlite3.Connection, account_id: str) -> dict[str, list[str]]:
    sides: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT instance_id, side FROM hassault_equips WHERE account_id = ?",
        (account_id,),
    ).fetchall():
        sides.setdefault(str(r["instance_id"]), []).append(str(r["side"]))
    return sides


def inventory(account_id: str) -> list[dict[str, Any]]:
    store.init_db()
    with store.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM hassault_items WHERE owner_id = ? AND state = 'owned' "
            "ORDER BY acquired_at",
            (account_id,),
        ).fetchall()
        sides = _sides_by_item(conn, account_id)
    return [_item_dict(r, sides.get(str(r["instance_id"]))) for r in rows]


def _insert_item(
    conn: sqlite3.Connection, account_id: str, instance: Any, origin: str, ref: str
) -> None:
    conn.execute(
        "INSERT INTO hassault_items (instance_id, owner_id, def_id, float_value, "
        "pattern_seed, stattrak, origin, tradable_after, acquired_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            instance.instance_id,
            account_id,
            instance.skin_id,
            instance.float_value,
            instance.pattern_seed,
            instance.stat_tracker_kills,
            origin,
            # Tradable a week after it was earned. Trading itself is P9; the
            # hold is recorded now so items earned today are not born exempt.
            time.time() + 7 * 86400,
            instance.acquired_at,
        ),
    )
    conn.execute(
        "INSERT INTO hassault_item_events (instance_id, kind, to_id, ref, at) "
        "VALUES (?, ?, ?, ?, ?)",
        (instance.instance_id, origin, account_id, ref, time.time()),
    )


def claim_drop(account_id: str) -> dict[str, Any] | None:
    """Spend one level's entitlement, or `None` when there is none to spend."""
    from backend.modules.hassault.skins import roll_instance

    status = progression(account_id)
    if status["available"] <= 0:
        return None
    instance = roll_instance()
    with store.get_conn() as conn:
        claimed = {
            int(r["level"])
            for r in conn.execute(
                "SELECT level FROM hassault_drop_claims WHERE account_id = ?",
                (account_id,),
            ).fetchall()
        }
        level = next(
            (lv for lv in range(2, status["level"] + 1) if lv not in claimed), None
        )
        if level is None:
            return None
        try:
            # The claim first: its primary key is the guard.
            conn.execute(
                "INSERT INTO hassault_drop_claims VALUES (?, ?, ?, ?)",
                (account_id, level, instance.instance_id, time.time()),
            )
        except sqlite3.IntegrityError:
            return None
        _insert_item(conn, account_id, instance, "drop", f"level:{level}")
        row = conn.execute(
            "SELECT * FROM hassault_items WHERE instance_id = ?",
            (instance.instance_id,),
        ).fetchone()
    out = _item_dict(row)
    out["remaining"] = progression(account_id)["available"]
    return out


def _sync_equipped(conn: sqlite3.Connection, account_id: str) -> None:
    """`hassault_items.equipped` means "on for some side". It is recomputed from
    the equips and never written on its own, so the two cannot disagree."""
    conn.execute(
        "UPDATE hassault_items SET equipped = (instance_id IN (SELECT instance_id "
        "FROM hassault_equips WHERE account_id = ?)) WHERE owner_id = ?",
        (account_id, account_id),
    )


def _sides(side: str | None) -> tuple[str, ...]:
    from backend.modules.hassault import weapons

    if not side:
        return weapons.SIDES
    if side not in weapons.SIDES:
        raise ValueError(f"unknown side {side!r}")
    return (side,)


def equip(account_id: str, instance_id: str, side: str | None = None) -> bool:
    """Put an owned item on its weapon for `side`, or for both sides when
    `side` is omitted, replacing whatever was there."""
    from backend.modules.hassault.skins import skin_dict

    sides = _sides(side)
    with store.get_conn() as conn:
        target = conn.execute(
            "SELECT def_id FROM hassault_items WHERE instance_id = ? AND owner_id = ? "
            "AND state = 'owned'",
            (instance_id, account_id),
        ).fetchone()
        definition = skin_dict().get(str(target["def_id"])) if target else None
        if definition is None:
            return False
        for each in sides:
            conn.execute(
                "INSERT INTO hassault_equips VALUES (?, ?, ?, ?) "
                "ON CONFLICT(account_id, side, weapon_id) "
                "DO UPDATE SET instance_id = excluded.instance_id",
                (account_id, each, definition.weapon_id, instance_id),
            )
        _sync_equipped(conn, account_id)
    return True


def unequip(account_id: str, weapon_id: str, side: str | None = None) -> bool:
    """Take the skin off `weapon_id` for `side` (or both): back to bare metal."""
    sides = _sides(side)
    with store.get_conn() as conn:
        removed = conn.execute(
            f"DELETE FROM hassault_equips WHERE account_id = ? AND weapon_id = ? "
            f"AND side IN ({','.join('?' * len(sides))})",
            (account_id, weapon_id, *sides),
        ).rowcount
        _sync_equipped(conn, account_id)
    return removed > 0


def cosmetics(account_id: str) -> dict[str, Any]:
    """How this account looks to everyone else (`cosmetics.bundle`), built from
    its own equips and its `hassault.agents` setting, nothing a client sent."""
    from backend.modules.hassault import cosmetics as cosmetics_mod

    store.init_db()
    with store.get_conn() as conn:
        rows = conn.execute(
            "SELECT e.side AS equip_side, i.* FROM hassault_equips e "
            "JOIN hassault_items i ON i.instance_id = e.instance_id "
            "WHERE e.account_id = ? AND i.owner_id = ? AND i.state = 'owned'",
            (account_id, account_id),
        ).fetchall()
    equips: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        equips.setdefault(str(r["equip_side"]), []).append(_item_dict(r))
    agents = settings_get(account_id)["values"].get(cosmetics_mod.AGENTS_KEY)
    return cosmetics_mod.bundle(cosmetics_mod.agents_for(agents), equips)


def loadouts(account_id: str) -> dict[str, dict[str, int]]:
    """The account's loadout per side, from its `hassault.loadout` setting."""
    from backend.modules.hassault import weapons

    return weapons.loadouts_for(
        settings_get(account_id)["values"].get("hassault.loadout")
    )


def trade_up(account_id: str, instance_ids: list[str]) -> dict[str, Any] | None:
    """Ten owned items of one rarity become one of the next, in one transaction."""
    import random
    import uuid

    from backend.modules.hassault.skins import (
        RARITY_ORDER,
        SkinInstance,
        catalog,
        skin_dict,
    )

    if len(set(instance_ids)) != 10:
        return None
    known = skin_dict()
    with store.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM hassault_items WHERE owner_id = ? AND state = 'owned' "
            f"AND instance_id IN ({','.join('?' * 10)})",
            (account_id, *instance_ids),
        ).fetchall()
        if len(rows) != 10:
            return None
        rarities = {getattr(known.get(str(r["def_id"])), "rarity", None) for r in rows}
        if len(rarities) != 1 or None in rarities:
            return None
        rarity = rarities.pop()
        at = RARITY_ORDER.index(rarity)
        if at >= len(RARITY_ORDER) - 1:
            return None
        pool = [s for s in catalog() if s.rarity == RARITY_ORDER[at + 1]]
        if not pool:
            return None
        made = SkinInstance(
            instance_id=str(uuid.uuid4()),
            skin_id=random.choice(pool).id,
            float_value=round(sum(float(r["float_value"]) for r in rows) / 10.0, 5),
            pattern_seed=random.randint(1, 1000),
            acquired_at=time.time(),
        )
        for r in rows:
            conn.execute(
                "UPDATE hassault_items SET state = 'consumed', equipped = 0 "
                "WHERE instance_id = ?",
                (r["instance_id"],),
            )
            conn.execute(
                "DELETE FROM hassault_equips WHERE instance_id = ?",
                (r["instance_id"],),
            )
            conn.execute(
                "INSERT INTO hassault_item_events (instance_id, kind, from_id, ref, at) "
                "VALUES (?, 'tradeup_burn', ?, ?, ?)",
                (r["instance_id"], account_id, made.instance_id, time.time()),
            )
        _insert_item(conn, account_id, made, "tradeup", "tradeup")
        row = conn.execute(
            "SELECT * FROM hassault_items WHERE instance_id = ?", (made.instance_id,)
        ).fetchone()
    return _item_dict(row)


# ---- import -------------------------------------------------------------------------


def import_node(
    account_id: str, node_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Bring one machine's local history in, once. See the module docstring."""
    items = list(payload.get("items") or [])[:MAX_IMPORT_ITEMS]
    matches = list(payload.get("matches") or [])[:MAX_IMPORT_MATCHES]
    store.init_db()
    now = time.time()
    with store.get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO hassault_imports VALUES (?, ?, ?, ?, ?)",
                (account_id, node_id, len(items), len(matches), now),
            )
        except sqlite3.IntegrityError:
            return {"imported": False, "reason": "this machine was already imported"}

        level_before = _level_of(_total_xp(conn, account_id))
        added_items = skipped = 0
        for item in items:
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO hassault_items (instance_id, owner_id, def_id, "
                    "float_value, pattern_seed, stattrak, origin, tradable_after, "
                    "equipped, acquired_at) VALUES (?, ?, ?, ?, ?, ?, 'import', NULL, ?, ?)",
                    (
                        str(item["instanceId"]),
                        account_id,
                        str(item["skinId"]),
                        max(0.0, min(1.0, float(item.get("floatValue", 0.5)))),
                        max(1, min(1000, int(item.get("patternSeed", 1)))),
                        item.get("statTrackerKills"),
                        1 if item.get("isEquipped") else 0,
                        float(item.get("acquiredAt") or now),
                    ),
                )
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue
            if cur.rowcount:
                added_items += 1
            else:
                # The same instance id from a copied database, or already here.
                skipped += 1
        added_xp = 0
        for match in matches:
            try:
                xp = max(0, int(match["xp"]))
                cur = conn.execute(
                    "INSERT OR IGNORE INTO hassault_xp_ledger VALUES (?, 'import', ?, ?, ?)",
                    (account_id, f"{node_id}:{match['id']}", xp, now),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if cur.rowcount:
                added_xp += xp
        level_after = _level_of(_total_xp(conn, account_id))
        # The levels the import reached are pre-claimed: no drop for them.
        for level in range(max(2, level_before + 1), level_after + 1):
            conn.execute(
                "INSERT OR IGNORE INTO hassault_drop_claims VALUES (?, ?, NULL, ?)",
                (account_id, level, now),
            )
    return {
        "imported": True,
        "items": added_items,
        "skipped": skipped,
        "xp": added_xp,
        "levels": [level_before, level_after],
    }


# ---- routes ---------------------------------------------------------------------


class SettingsPut(BaseModel):
    values: dict[str, Any]


class ImportBody(BaseModel):
    nodeId: str  # noqa: N815
    items: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []


class TradeUpBody(BaseModel):
    instanceIds: list[str]  # noqa: N815


@router.get("/account/settings")
def get_settings(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return settings_get(_account(authorization))


async def _changed(account_id: str) -> None:
    """Tell any room this account is seated in that it looks different now."""
    from backend.games_server import hassault_rooms

    await hassault_rooms.referee.refresh_account(account_id)


@router.put("/account/settings")
async def put_settings(
    body: SettingsPut, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    from backend.modules.hassault import cosmetics as cosmetics_mod

    account_id = _account(authorization)
    out = settings_put(account_id, body.values)
    if {"hassault.loadout", cosmetics_mod.AGENTS_KEY} & set(body.values):
        await _changed(account_id)
    return out


@router.get("/account/progression")
def get_progression(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return progression(_account(authorization))


@router.post("/account/import")
def post_import(
    body: ImportBody, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    node = body.nodeId.strip()[:128]
    if not node:
        raise HTTPException(status_code=400, detail="nodeId is required")
    return import_node(
        _account(authorization), node, {"items": body.items, "matches": body.matches}
    )


@router.get("/account/inventory")
def get_inventory(
    authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    return inventory(_account(authorization))


@router.get("/account/drops")
def get_drops(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return progression(_account(authorization))


@router.post("/account/claim_drop")
def post_claim(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    account_id = _account(authorization)
    drop = claim_drop(account_id)
    if drop is None:
        status = progression(account_id)
        raise HTTPException(
            status_code=409,
            detail=(
                f"No level-up drop available. You are level {status['level']}; "
                f"{status['xpToNextDrop']} XP earns the next one."
            ),
        )
    return drop


@router.post("/account/equip")
async def post_equip(
    instance_id: str,
    side: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    account_id = _account(authorization)
    try:
        ok = equip(account_id, instance_id, side)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if ok:
        await _changed(account_id)
    return {"ok": ok}


@router.post("/account/unequip")
async def post_unequip(
    weapon_id: str,
    side: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    account_id = _account(authorization)
    try:
        ok = unequip(account_id, weapon_id, side)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if ok:
        await _changed(account_id)
    return {"ok": ok}


@router.get("/account/cosmetics")
def get_cosmetics(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return cosmetics(_account(authorization))


@router.post("/account/tradeup")
def post_tradeup(
    body: TradeUpBody, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    made = trade_up(_account(authorization), body.instanceIds)
    if made is None:
        raise HTTPException(
            status_code=400,
            detail="Trade-Up Contract requires exactly 10 owned items of the same rarity tier.",
        )
    return made
