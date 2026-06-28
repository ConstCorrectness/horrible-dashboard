import httpx
import asyncio


async def test_run(temp):
    url = "http://localhost:11434/api/chat"

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_current_weather",
                "description": "Get the current weather",
                "parameters": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                },
            },
        }
    ]

    payload = {
        "model": "gemma4:e2b",
        "messages": [
            {"role": "user", "content": "Think about the number 42 as hard as you can"}
        ],
        "tools": tools,
        "think": True,
        "options": {"temperature": temp},
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(url, json=payload)
        data = res.json()
        msg = data.get("message", {})
        print(f"temp={temp} -> Keys: {list(msg.keys())}")
        if "thinking" in msg:
            print(f"  Thinking: {repr(msg['thinking'][:100])}...")
        print(f"  Content length: {len(msg.get('content', ''))}")
        print(f"  Content snippet: {repr(msg.get('content', '')[:100])}...")


async def main():
    print("--- Running determinism check ---")
    print("Run 1 (temp=0.0):")
    await test_run(0.0)
    print("Run 2 (temp=0.0):")
    await test_run(0.0)
    print("Run 3 (temp=0.7):")
    await test_run(0.7)
    print("Run 4 (temp=0.7):")
    await test_run(0.7)


if __name__ == "__main__":
    asyncio.run(main())
