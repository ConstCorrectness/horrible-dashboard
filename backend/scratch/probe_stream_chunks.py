import httpx
import json
import asyncio

async def main():
    url = "http://localhost:11434/api/chat"
    payload = {
        "model": "gemma4:e2b",
        "messages": [{"role": "user", "content": "Think about the number 42 as hard as you can"}],
        "options": {
            "temperature": 0.7
        },
        "stream": True,
        "think": True
    }
    
    print("Sending streaming request to Ollama...")
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", url, json=payload) as res:
            print(f"Status: {res.status_code}")
            count = 0
            async for line in res.aiter_lines():
                if not line:
                    continue
                count += 1
                if count <= 15 or count % 50 == 0:
                    try:
                        obj = json.loads(line)
                        msg = obj.get("message", {})
                        print(f"Chunk #{count}: keys={list(obj.keys())}, message_keys={list(msg.keys())}")
                        if "thinking" in msg:
                            print(f"  -> Thinking delta: {repr(msg['thinking'])}")
                        if "content" in msg:
                            print(f"  -> Content delta: {repr(msg['content'])}")
                    except Exception as e:
                        print(f"Chunk #{count} error: {e}, raw line: {line}")
            print(f"Total chunks: {count}")

if __name__ == "__main__":
    asyncio.run(main())
