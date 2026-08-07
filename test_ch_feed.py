import asyncio
import json
import os
import httpx

async def main():
    data_dir = os.environ.get("HORRIBLE_DATA_DIR", ".data")
    auth_file = f"{data_dir}/clubhouse-auth.json"
    with open(auth_file, "r") as f:
        auth = json.load(f)
        
    token = auth["auth_token"]
    user_id = auth["user_id"]
    device_id = auth.get("device_id", "test_device")
    
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
        cursor = None
        total = 0
        for i in range(4):
            payload = {}
            if cursor:
                payload["cursor"] = cursor
            res = await client.post("https://www.clubhouseapi.com/api/get_feed_v3", headers=headers, json=payload)
            data = res.json()
            items = data.get("items", [])
            print(f"Page {i+1}: {len(items)} items")
            total += len(items)
            cursor = data.get("cursor")
            if not cursor:
                print("No cursor found.")
                break
        print(f"Total: {total}")
            
asyncio.run(main())
