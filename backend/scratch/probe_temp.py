import httpx
import asyncio


async def test_probe(temp):
    url = "http://localhost:11434/api/chat"
    payload = {
        "model": "gemma4:e2b",
        "messages": [
            {"role": "user", "content": "Think about the number 42 as hard as you can"}
        ],
        "options": {"temperature": temp},
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(url, json=payload)
        data = res.json()
        msg = data.get("message", {})
        has_thinking = "thinking" in msg
        print(f"Temp {temp}: Has thinking key: {has_thinking}")
        print(f"Content length: {len(msg.get('content', ''))}")
        if has_thinking:
            print(f"Thinking: {repr(msg['thinking'][:100])}...")
        else:
            print("No thinking key returned in message!")
            # Also check if it's inline in content
            content = msg.get("content", "")
            if "<think>" in content:
                print("Found <think> tag inside content!")
            else:
                print("No <think> tag inside content.")


async def main():
    print("Running probes...")
    await test_probe(0.0)
    await test_probe(0.7)


if __name__ == "__main__":
    asyncio.run(main())
