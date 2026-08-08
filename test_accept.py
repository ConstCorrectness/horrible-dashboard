import asyncio
from backend.modules.clubhouse.routes import _ch_authed_post, _require_auth

async def main():
    auth = _require_auth()
    feed = await _ch_authed_post("/get_feed_v3", {}, auth["auth_token"], auth["user_id"], auth.get("device_id"))
    channel_name = None
    for item in feed.get("items", []):
        if "channel" in item:
            channel_name = item["channel"]["channel"]
            break
            
    if not channel_name:
        print("No channel found")
        return

    print(f"Testing accept_speaker for {channel_name}...")
    endpoints = [
        "/accept_speaker",
        "/accept_speaker_invite",
        "/accept_invite",
        "/channels/accept_speaker",
    ]
    for ep in endpoints:
        print(f"Trying {ep}...")
        try:
            res = await _ch_authed_post(
                ep,
                {"channel": channel_name, "user_id": auth["user_id"]},
                auth["auth_token"],
                auth["user_id"],
                auth.get("device_id"),
            )
            print("Success:", res)
            return
        except Exception as e:
            if "404" in str(e):
                print("Endpoint 404")
            else:
                print("Failed:", e)

if __name__ == "__main__":
    asyncio.run(main())
