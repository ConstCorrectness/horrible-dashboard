import httpx
import asyncio


async def test_probe(think, temp):
    url = "http://localhost:11434/api/chat"
    payload = {
        "model": "gemma4:e2b",
        "messages": [
            {"role": "user", "content": "Think about the number 42 as hard as you can"}
        ],
        "options": {"temperature": temp},
        "stream": False,
    }
    if think is not None:
        payload["think"] = think

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(url, json=payload)
        data = res.json()
        msg = data.get("message", {})
        keys = list(msg.keys())
        print(f"think={think}, temp={temp} -> JSON keys in message: {keys}")
        if "thinking" in msg:
            print(f"  Thinking: {repr(msg['thinking'][:100])}...")
        print(f"  Content length: {len(msg.get('content', ''))}")
        print(f"  Content snippet: {repr(msg.get('content', '')[:100])}...")


async def main():
    print("Running raw Ollama probes...")
    print("--- Probing think=True ---")
    await test_probe(True, 0.0)
    await test_probe(True, 0.7)

    print("\n--- Probing think=False ---")
    await test_probe(False, 0.0)
    await test_probe(False, 0.7)

    print("\n--- Probing think=None (default) ---")
    await test_probe(None, 0.0)
    await test_probe(None, 0.7)


if __name__ == "__main__":
    asyncio.run(main())
