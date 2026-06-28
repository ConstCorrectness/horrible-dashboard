import httpx
import asyncio

SYSTEM_PROMPT = (
    "You are the orchestrator for horrible-dashboard, a dockable dashboard app. "
    "You arrange the user's screen by opening/closing panes, by splitting, "
    "resizing, moving, floating and maximizing them, and by managing workspaces, "
    "using the provided tools.\n"
    "Rules:\n"
    "- The screen layout is composed of 'panes' (layout containers) hosting 'views' "
    "(registered panels or widgets, e.g. Marketplace, Settings, Data flow, Backend status). "
    "To open/show/add a view, FIRST call list_available_panes to find the view whose title "
    "matches, THEN call open_pane with that view ID. Do NOT treat it as a workspace.\n"
    "- To arrange active panes (split/resize/move/float/maximize), FIRST call list_open_panes "
    "to get each active pane's live instanceId. Geometry tools take the instanceId, NOT the "
    "view ID. split_pane also needs a view ID (paneId parameter, from list_available_panes) "
    "for the view content to show in the new split pane.\n"
    "- If the user refers to a pane via a title, file name, or instance ID (e.g., 'pane:main.py' or 'pane:editor.buffer#1'), "
    "match it against the title or instanceId of the open panes returned by list_open_panes to find the "
    "correct active instanceId to target.\n"
    "- If the user refers to a file via an absolute or relative path prefixed with '@' (e.g., '@absolute_path'), "
    "use that path directly with files/editor tools.\n"
    "- Only use list_workspaces / create_workspace / switch_workspace when the "
    "user explicitly talks about workspaces or tabs.\n"
    "- Ids are not guessable; always discover them with a list_* tool first.\n"
    "- When the user asks ABOUT what's on screen, the layout, or a view's "
    "contents, call list_open_panes first, then get_pane_context on the relevant "
    "pane(s), and answer from what they return — do not guess.\n"
    "- To change code in an open editor buffer (format, rewrite, fix), use "
    "editor.proposeEdit (NOT editor.applyEdit) so the user reviews the diff and "
    "accepts or declines it.\n"
    "- After acting, reply with one short sentence confirming what you did."
)


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
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Think about the number 42 as hard as you can"},
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
    print("--- Probing with SYSTEM_PROMPT ---")
    await test_run(0.0)
    await test_run(0.7)


if __name__ == "__main__":
    asyncio.run(main())
