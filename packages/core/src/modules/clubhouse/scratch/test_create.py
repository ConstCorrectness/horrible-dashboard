import asyncio
import json
from pathlib import Path
from backend.modules.clubhouse.routes import _ch_authed_post


async def main():
    # Load credentials
    auth_path = Path("/home/horrible/horrible-dashboard/.data/clubhouse-auth.json")
    auth = json.loads(auth_path.read_text())
    token = auth["auth_token"]
    user_id = auth["user_id"]
    device_id_path = Path("/home/horrible/horrible-dashboard/.data/clubhouse-device-id")
    device_id = device_id_path.read_text().strip() if device_id_path.is_file() else "D"

    # Let's test create_channel with different payloads
    payloads = [
        {
            "topic": "T1",
            "privacy_level": "house",
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        {
            "topic": "T2",
            "privacy_level": "mutuals",
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        {
            "topic": "T3",
            "privacy_level": "club",
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        {
            "topic": "T4",
            "privacy_level": "members",
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        {
            "topic": "T5",
            "privacy_level": "invite",
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        {
            "topic": "T6",
            "privacy_level": "invited",
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        {
            "topic": "T7",
            "privacy_level": "open",
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
    ]

    for idx, p in enumerate(payloads, start=1):
        print(f"\n--- Testing Payload {idx} ---")
        print(json.dumps(p, indent=2))
        try:
            res = await _ch_authed_post("/create_channel", p, token, user_id, device_id)
            print("SUCCESS! Channel response:")
            print(res)
            # If succeeded, let's leave it immediately
            if res.get("channel"):
                print(f"Leaving channel {res['channel']}...")
                await _ch_authed_post(
                    "/leave_channel",
                    {"channel": res["channel"]},
                    token,
                    user_id,
                    device_id,
                )
        except Exception as e:
            print("FAILED:", str(e))


if __name__ == "__main__":
    asyncio.run(main())
