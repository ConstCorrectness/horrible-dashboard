import json
import httpx
import asyncio
import os
import random

LOG_PATH = "/home/horrible/.gemini/antigravity-cli/brain/4cfe66aa-aa2e-49be-8534-f34d0f9d55e7/.system_generated/tasks/task-1284.log"

async def test_payload(name, payload):
    url = "http://localhost:11434/api/chat"
    payload = dict(payload)
    payload["stream"] = True
    payload["think"] = True
    
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            async with client.stream("POST", url, json=payload) as res:
                if res.status_code != 200:
                    return False
                async for line in res.aiter_lines():
                    if not line:
                        continue
                    obj = json.loads(line)
                    msg = obj.get("message", {})
                    if "thinking" in msg:
                        return True
                    if "content" in msg and msg["content"]:
                        return False
                return False
        except Exception as e:
            return False

async def main():
    if not os.path.exists(LOG_PATH):
        print(f"Log path does not exist: {LOG_PATH}")
        return
        
    with open(LOG_PATH, "r") as f:
        for line in f:
            if "Ollama Request Payload:" in line:
                idx = line.find("Ollama Request Payload:")
                json_str = line[idx + len("Ollama Request Payload:"):].strip()
                payload = json.loads(json_str)
                break
                
    original_tools = payload.get("tools", [])
    print(f"Number of tools: {len(original_tools)}")
    
    # Print the tools from index 30 to 45
    print("\n--- Tools from index 30 to 45 ---")
    for idx, t in enumerate(original_tools[30:46], start=30):
        print(f"Index {idx}: {t['function']['name']}")
        
    # Test counts from 35 to 40
    print("\n--- Testing tool counts from 35 to 40 ---")
    for count in range(35, 41):
        test_p = dict(payload)
        test_p["tools"] = original_tools[:count]
        has_thinking = await test_payload(f"{count} Tools", test_p)
        print(f"{count} tools: Has thinking = {has_thinking}")
        
    # Shuffled tests to see if it's size or specific tools
    print("\n--- Shuffled tests (shuffling all tools, testing with 35, 36, 37, 38, 39, 40) ---")
    shuffled = list(original_tools)
    random.seed(42)
    random.shuffle(shuffled)
    for count in range(35, 41):
        test_p = dict(payload)
        test_p["tools"] = shuffled[:count]
        has_thinking = await test_payload(f"Shuffled {count} Tools", test_p)
        print(f"Shuffled {count} tools: Has thinking = {has_thinking}")

if __name__ == "__main__":
    asyncio.run(main())
