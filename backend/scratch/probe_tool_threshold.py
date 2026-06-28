import json
import httpx
import asyncio
import os

LOG_PATH = "/home/horrible/.gemini/antigravity-cli/brain/4cfe66aa-aa2e-49be-8534-f34d0f9d55e7/.system_generated/tasks/task-1284.log"


async def test_payload(name, payload):
    url = "http://localhost:11434/api/chat"
    # Ensure stream is True because we want to see if it streams thinking deltas.
    payload = dict(payload)
    payload["stream"] = True
    payload["think"] = True

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            async with client.stream("POST", url, json=payload) as res:
                if res.status_code != 200:
                    print(f"[{name}] HTTP Error {res.status_code}")
                    return False

                has_thinking = False
                first_content = None
                has_content = False
                chunks_read = 0

                async for line in res.aiter_lines():
                    if not line:
                        continue
                    chunks_read += 1
                    obj = json.loads(line)
                    msg = obj.get("message", {})
                    if "thinking" in msg:
                        has_thinking = True
                    if "content" in msg and msg["content"]:
                        has_content = True
                        if first_content is None:
                            first_content = msg["content"]
                    # If we have seen both, or we've read a few chunks and seen content first, we can exit early.
                    if chunks_read > 20:
                        break

                print(
                    f"[{name}] Has thinking: {has_thinking}, Has content: {has_content}, First content: {repr(first_content)}"
                )
                return has_thinking
        except Exception as e:
            print(f"[{name}] Request failed: {e}")
            return False


async def main():
    if not os.path.exists(LOG_PATH):
        print(f"Log path does not exist: {LOG_PATH}")
        return

    print("Reading log file to extract the real Ollama request payload...")
    payload = None
    with open(LOG_PATH, "r") as f:
        for line in f:
            if "Ollama Request Payload:" in line:
                idx = line.find("Ollama Request Payload:")
                json_str = line[idx + len("Ollama Request Payload:") :].strip()
                payload = json.loads(json_str)
                break

    if not payload:
        print("Could not find Ollama Request Payload in logs.")
        return

    original_tools = payload.get("tools", [])
    print(
        f"Found original payload. Model: {payload.get('model')}, Number of tools: {len(original_tools)}"
    )

    # Test 1: Original full payload
    print("\n--- Test 1: Original Payload ---")
    await test_payload("Full Payload", payload)

    # Test 2: Original payload without tools
    print("\n--- Test 2: Original Payload (No Tools) ---")
    no_tools_payload = dict(payload)
    no_tools_payload["tools"] = []
    await test_payload("No Tools", no_tools_payload)

    # Test 3: Binary search on number of tools
    print("\n--- Test 3: Binary search on number of tools ---")
    for count in [5, 10, 15, 20, 25, 30, 35, 40]:
        test_p = dict(payload)
        test_p["tools"] = original_tools[:count]
        await test_payload(f"{count} Tools", test_p)

    # Test 4: Check if tools containing dots (e.g., "clubhouse.status") cause it
    print("\n--- Test 4: Filter out tools with dots in names ---")
    no_dots_tools = [t for t in original_tools if "." not in t["function"]["name"]]
    test_p = dict(payload)
    test_p["tools"] = no_dots_tools
    print(f"Number of tools without dots: {len(no_dots_tools)}")
    await test_payload(f"No dots ({len(no_dots_tools)} tools)", test_p)

    # Test 5: Check if any specific tool names cause it (e.g. by using only dot-containing tools but fewer)
    print("\n--- Test 5: Only dot tools (count limited) ---")
    dot_tools = [t for t in original_tools if "." in t["function"]["name"]]
    test_p = dict(payload)
    test_p["tools"] = dot_tools[:10]
    await test_payload("10 dot tools", test_p)


if __name__ == "__main__":
    asyncio.run(main())
