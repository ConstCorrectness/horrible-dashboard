import httpx
import asyncio


async def main():
    url = "http://localhost:11434/api/chat"

    # Simple mock tool
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
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(url, json=payload)
        print(f"Status code: {res.status_code}")
        try:
            data = res.json()
            msg = data.get("message", {})
            print(f"Keys: {list(msg.keys())}")
            print(f"Content: {repr(msg.get('content', '')[:100])}...")
            if "thinking" in msg:
                print("Thinking was returned!")
            else:
                print("Thinking was NOT returned!")
        except Exception as e:
            print(f"Failed to parse json: {e}")
            print(res.text)


if __name__ == "__main__":
    asyncio.run(main())
