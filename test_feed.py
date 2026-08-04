import asyncio
from backend.modules.clubhouse.routes import channels, _require_auth
async def main():
    res = await channels()
    print("Channels count:", len(res["channels"]))

asyncio.run(main())
