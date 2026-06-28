import httpx
import asyncio
from backend.modules.agent.orchestrator import LAYOUT_TOOLS, SYSTEM_PROMPT


async def test_payload(name, messages, tools):
    url = "http://localhost:11434/api/chat"
    payload = {
        "model": "gemma4:e2b",
        "messages": messages,
        "tools": tools,
        "options": {"temperature": 0.7},
        "stream": False,
        "think": True,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(url, json=payload)
        data = res.json()
        msg = data.get("message", {})
        has_thinking = "thinking" in msg
        print(
            f"[{name}] Has thinking: {has_thinking}, Content snippet: {repr(msg.get('content', '')[:60])}"
        )


async def main():
    # 1. Standard one system message + user message (No tools)
    await test_payload(
        "1. One System + User (No Tools)",
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Think about the number 42 as hard as you can"},
        ],
        [],
    )

    # 2. Standard one system message + user message + tools
    await test_payload(
        "2. One System + User + Tools",
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Think about the number 42 as hard as you can"},
        ],
        LAYOUT_TOOLS,
    )

    # 3. Two system messages + user message (No tools)
    await test_payload(
        "3. Two Systems + User (No Tools)",
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": "The user is editing main.py. Content: print('hello')",
            },
            {"role": "user", "content": "Think about the number 42 as hard as you can"},
        ],
        [],
    )

    # 4. Two system messages + user message + tools
    await test_payload(
        "4. Two Systems + User + Tools",
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": "The user is editing main.py. Content: print('hello')",
            },
            {"role": "user", "content": "Think about the number 42 as hard as you can"},
        ],
        LAYOUT_TOOLS,
    )

    # 5. System message + user message + assistant message + user message + tools (History)
    await test_payload(
        "5. One System + History + Tools",
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hello! How can I help you today?"},
            {"role": "user", "content": "Think about the number 42 as hard as you can"},
        ],
        LAYOUT_TOOLS,
    )


if __name__ == "__main__":
    asyncio.run(main())
