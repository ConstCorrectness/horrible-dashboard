import asyncio, json
from backend.modules.clubhouse.routes import _ch_authed_post, _require_auth

async def main():
    auth = _require_auth()
    raw = await _ch_authed_post("/get_feed_v3", {}, auth["auth_token"], auth["user_id"], auth.get("device_id"))
    next_cursor = raw.get("next_cursor")
    print("Page 1 items:", len(raw.get("items", [])))
    if next_cursor:
        raw2 = await _ch_authed_post("/get_feed_v3", {"cursor": next_cursor}, auth["auth_token"], auth["user_id"], auth.get("device_id"))
        print("Page 2 items:", len(raw2.get("items", [])))
    else:
        print("No next_cursor")
    
asyncio.run(main())
