import asyncio
import json
import os
import httpx

async def main():
    data_dir = os.environ.get("HORRIBLE_DATA_DIR", ".data")
    auth_file = f"{data_dir}/clubhouse-auth.json"
    if not os.path.exists(auth_file):
        return
    with open(auth_file, "r") as f:
        auth = json.load(f)
    token = auth["auth_token"]
    user_id = auth["user_id"]
    device_id = auth.get("device_id", "test")
    headers = {
        "CH-Languages": "en-US",
        "CH-Locale": "en_US",
        "CH-AppBuild": "26.07.07",
        "CH-AppVersion": "26.07.07",
        "CH-DeviceId": device_id,
        "User-Agent": "clubhouse/android/26.07.07",
        "Authorization": f"Token {token}",
        "CH-UserID": str(user_id),
    }
    
    async with httpx.AsyncClient(timeout=15) as client:
        # First get feed to find an active room
        res = await client.post("https://www.clubhouseapi.com/api/get_feed_v3", headers=headers, json={})
        items = res.json().get("items", [])
        channel = None
        for item in items:
            if "channel" in item:
                channel = item["channel"]["channel"]
                break
        
        if not channel:
            print("No channels found")
            return
            
        print(f"Testing chat for channel {channel}")
        res = await client.post("https://www.clubhouseapi.com/api/get_channel", headers=headers, json={"channel": channel})
        recent_messages = res.json().get("recent_messages", [])
        print(f"Found {len(recent_messages)} recent messages")
        if recent_messages:
            print(json.dumps(recent_messages[0], indent=2))
            
asyncio.run(main())
