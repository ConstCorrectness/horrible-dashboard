import httpx
import json
import asyncio
from backend.modules.agent.orchestrator import LAYOUT_TOOLS, SYSTEM_PROMPT

async def main():
    url = "http://localhost:11434/api/chat"
    
    payload = {
        "model": "gemma4:e2b",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Think about the number 42 as hard as you can"}
        ],
        "tools": LAYOUT_TOOLS,
        "think": True,
        "options": {
            "temperature": 0.7
        },
        "stream": False
    }
    
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(url, json=payload)
        data = res.json()
        msg = data.get("message", {})
        print(f"Status code: {res.status_code}")
        print(f"Keys: {list(msg.keys())}")
        if "thinking" in msg:
            print(f"  Thinking: {repr(msg['thinking'][:100])}...")
        print(f"  Content length: {len(msg.get('content', ''))}")
        print(f"  Content snippet: {repr(msg.get('content', '')[:100])}...")

if __name__ == "__main__":
    asyncio.run(main())
