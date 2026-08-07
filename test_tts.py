import asyncio
from backend.modules.agent.xtts_service import xtts_service
async def main():
    try:
        audio = await xtts_service.generate_audio("Hello, this is a test.")
        print(f"Generated {len(audio)} bytes of audio.")
    except Exception as e:
        print(f"Error: {e}")
asyncio.run(main())
