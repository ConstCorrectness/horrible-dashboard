#!/usr/bin/env python3
"""Attach to running Chrome/Chromium via Chrome DevTools Protocol (CDP) and stream live console logs.

Usage:
  python3 scripts/attach_browser_console.py [--port 9222] [--target play-cs,game]

Prerequisites:
  Start Chrome with remote debugging:
    google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug <URL>
"""

import argparse
import asyncio
import json
import sys
import urllib.request

try:
    import websockets
except ImportError:
    print("[!] The 'websockets' Python package is required.")
    print("    Install it via: pip install websockets")
    sys.exit(1)


async def monitor_tab(tab_id: str, ws_url: str, title: str, url: str) -> None:
    print(f"\n[+] Attached to tab: {title} ({url})")
    print(f"[+] WebSocket endpoint: {ws_url}")
    print("-" * 65)
    try:
        async with websockets.connect(ws_url, ping_interval=None) as ws:
            # Enable Runtime, Console, and Log CDP domains
            await ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
            await ws.send(json.dumps({"id": 2, "method": "Console.enable"}))
            await ws.send(json.dumps({"id": 3, "method": "Log.enable"}))

            # Query title and URL verification
            await ws.send(
                json.dumps({
                    "id": 4,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": "console.log('[ATTACHED] Title:', document.title, 'URL:', location.href);"
                    },
                })
            )

            async for message in ws:
                data = json.loads(message)
                method = data.get("method")
                if method == "Runtime.consoleAPICalled":
                    params = data.get("params", {})
                    msg_type = params.get("type", "log").upper()
                    args = [
                        str(a.get("value", a.get("description", "")))
                        for a in params.get("args", [])
                    ]
                    text = " ".join(args)
                    print(f"[{msg_type}] {text}")
                elif method == "Log.entryAdded":
                    entry = data.get("params", {}).get("entry", {})
                    level = entry.get("level", "info").upper()
                    text = entry.get("text", "")
                    # Filter noisy ad tracker 404s
                    if "404" not in text and "doubleclick" not in text:
                        print(f"[{level}] {text}")
    except Exception as e:
        print(f"[*] Disconnected from tab '{title}': {e}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stream live console logs from Chrome tabs via CDP."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9222,
        help="Chrome remote debugging port (default: 9222)",
    )
    parser.add_argument(
        "--target",
        type=str,
        default="play-cs,game",
        help="Comma-separated substrings to match in URL or title (default: 'play-cs,game'). Pass '*' for all.",
    )
    args = parser.parse_args()

    json_url = f"http://127.0.0.1:{args.port}/json"
    keywords = [k.strip() for k in args.target.split(",")] if args.target != "*" else None

    print(f"[*] Watching Chrome tabs on {json_url} (filter: {args.target})...")
    attached_tabs = set()

    while True:
        try:
            with urllib.request.urlopen(json_url, timeout=3) as resp:
                tabs = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[!] Cannot poll tabs from {json_url}: {e}")
            print(
                f"[!] Ensure Chrome is running with: google-chrome --remote-debugging-port={args.port} --user-data-dir=/tmp/chrome-debug"
            )
            await asyncio.sleep(2)
            continue

        for tab in tabs:
            if tab.get("type") != "page":
                continue
            tab_id = tab.get("id")
            url = tab.get("url", "")
            title = tab.get("title", "")
            ws_url = tab.get("webSocketDebuggerUrl")

            # Check matching keywords
            matches = (
                True
                if keywords is None
                else any(k in url or k in title for k in keywords)
            )

            if tab_id not in attached_tabs and ws_url and matches:
                attached_tabs.add(tab_id)
                asyncio.create_task(monitor_tab(tab_id, ws_url, title, url))

        await asyncio.sleep(1.5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[*] Exiting console logger.")
