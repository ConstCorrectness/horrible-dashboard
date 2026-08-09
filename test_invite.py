import asyncio
from backend.modules.clubhouse.routes import _ch_authed_post, _require_auth

async def main():
    auth = _require_auth()
    # just print to see if we can do something
    print(auth)

asyncio.run(main())
