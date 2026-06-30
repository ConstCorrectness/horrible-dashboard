import json
import httpx
import asyncio
import os

LOG_PATH = "/home/horrible/.gemini/antigravity-cli/brain/4cfe66aa-aa2e-49be-8534-f34d0f9d55e7/.system_generated/tasks/task-1284.log"


def prune_tools(original_tools, prompt, layout_tools_count=15):
    # Separate layout tools (they are first 15)
    merged = original_tools[:layout_tools_count]
    dynamic_tools = original_tools[layout_tools_count:]

    total_count = len(merged) + len(dynamic_tools)
    if total_count <= 38:
        return merged + dynamic_tools

    text_to_search = prompt.lower()

    groups = {
        "visualizer": {
            "prefixes": ("visualizer.",),
            "keywords": (
                "visualizer",
                "render",
                "pygame",
                "canvas",
                "three",
                "babylon",
                "draw",
                "animation",
            ),
        },
        "database": {
            "prefixes": ("database.",),
            "keywords": (
                "database",
                "sql",
                "query",
                "vector",
                "semantic",
                "embeddings",
                "db search",
            ),
        },
        "clubhouse": {
            "prefixes": ("clubhouse.",),
            "keywords": ("clubhouse", "room", "disconnect"),
        },
        "stub": {"prefixes": ("stub.",), "keywords": ("stub", "getvalue", "setvalue")},
    }

    active_prefixes = set()
    for gname, ginfo in groups.items():
        if any(kw in text_to_search for kw in ginfo["keywords"]):
            active_prefixes.update(ginfo["prefixes"])

    core_prefixes = ("files.", "editor.", "terminal.")

    def get_priority(t):
        name = t["function"]["name"]
        if any(name.startswith(p) for p in active_prefixes):
            return 0
        if any(name.startswith(p) for p in core_prefixes):
            return 1
        if name.startswith("agent.") or name.startswith("observability."):
            return 2
        return 3

    dynamic_tools.sort(key=get_priority)
    selected_dynamic = dynamic_tools[:23]
    return merged + selected_dynamic


async def test_payload(name, payload):
    url = "http://localhost:11434/api/chat"
    payload = dict(payload)
    payload["stream"] = True
    payload["think"] = True

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            async with client.stream("POST", url, json=payload) as res:
                if res.status_code != 200:
                    print(f"[{name}] HTTP Error {res.status_code}")
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
            print(f"[{name}] Request failed: {e}")
            return False


async def main():
    if not os.path.exists(LOG_PATH):
        print(f"Log path does not exist: {LOG_PATH}")
        return

    with open(LOG_PATH, "r") as f:
        for line in f:
            if "Ollama Request Payload:" in line:
                idx = line.find("Ollama Request Payload:")
                json_str = line[idx + len("Ollama Request Payload:") :].strip()
                payload = json.loads(json_str)
                break

    original_tools = payload.get("tools", [])
    print(f"Original number of tools: {len(original_tools)}")

    # Case 1: Simple prompt (no special keywords)
    prompt1 = "Think about the number 42 as hard as you can"
    pruned1 = prune_tools(original_tools, prompt1)
    print(f"\nPrompt: '{prompt1}' -> Tools count: {len(pruned1)}")
    print(f"Selected tools: {[t['function']['name'] for t in pruned1]}")
    payload1 = dict(payload)
    payload1["messages"] = [{"role": "user", "content": prompt1}]
    payload1["tools"] = pruned1
    has_thinking1 = await test_payload("Pruned (no keywords)", payload1)
    print(f"Has thinking: {has_thinking1}")

    # Case 2: Prompt asking for the database
    prompt2 = "Please run a SQL query against my database for 'ai projects'"
    pruned2 = prune_tools(original_tools, prompt2)
    print(f"\nPrompt: '{prompt2}' -> Tools count: {len(pruned2)}")
    print(f"Selected tools: {[t['function']['name'] for t in pruned2]}")
    # Verify database tools are included
    database_included = any(
        t["function"]["name"].startswith("database.") for t in pruned2
    )
    print(f"Database tools included: {database_included}")
    payload2 = dict(payload)
    payload2["messages"] = [{"role": "user", "content": prompt2}]
    payload2["tools"] = pruned2
    has_thinking2 = await test_payload("Pruned (database keyword)", payload2)
    print(f"Has thinking: {has_thinking2}")


if __name__ == "__main__":
    asyncio.run(main())
