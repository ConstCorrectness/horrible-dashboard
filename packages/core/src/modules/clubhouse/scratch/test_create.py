import asyncio
import json
import sys
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
        # Original
        {
            "topic": "Test Room 1",
            "is_private": False,
            "is_social_mode": False,
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        # Adding privacy_level as string
        {
            "topic": "Test Room 2",
            "is_private": False,
            "is_social_mode": False,
            "privacy_level": "public",
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        # Adding privacy_level as integer 1
        {
            "topic": "Test Room 3",
            "is_private": False,
            "is_social_mode": False,
            "privacy_level": 1,
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        # Adding privacy_level as integer 2
        {
            "topic": "Test Room 4",
            "is_private": False,
            "is_social_mode": False,
            "privacy_level": 2,
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        # Let's try "privacy" field
        {
            "topic": "Test Room 5",
            "is_private": False,
            "is_social_mode": False,
            "privacy": "public",
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        # Let's try privacy: 1
        {
            "topic": "Test Room 6",
            "is_private": False,
            "is_social_mode": False,
            "privacy": 1,
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        }
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
                await _ch_authed_post("/leave_channel", {"channel": res["channel"]}, token, user_id, device_id)
        except Exception as e:
            print("FAILED:", str(e))

if __name__ == "__main__":
    asyncio.run(main())
