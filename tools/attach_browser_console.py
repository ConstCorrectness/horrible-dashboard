#!/usr/bin/env python3
"""Attach to a running Chrome/Chromium browser via Chrome DevTools Protocol (CDP).

Connects to http://127.0.0.1:9222/json, discovers tabs matching target titles/URLs
(e.g. play-cs.com, game.play-cs.com, horrible-games, localhost:5173), attaches via
WebSocket, enables Console & Runtime domains, and streams logs/events in real time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
import websockets


def find_targets(host: str, port: int, filter_str: str) -> list[dict]:
    url = f"http://{host}:{port}/json"
    try:
        with urllib.request.urlopen(url, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[-] Could not query Chrome debugging port at {url}: {exc}", file=sys.stderr)
        print("    Ensure Chrome was started with: google-chrome --remote-debugging-port=9222", file=sys.stderr)
        return []

    targets = []
    for item in data:
        if item.get("type") != "page":
            continue
        title = item.get("title", "").lower()
        target_url = item.get("url", "").lower()
        if not filter_str or filter_str.lower() in title or filter_str.lower() in target_url:
            targets.append(item)
    return targets


async def stream_tab(ws_url: str, title: str, log_file: str | None = None) -> None:
    print(f"[+] Connecting to tab: {title}")
    print(f"    WebSocket URL: {ws_url}")
    out_fh = open(log_file, "a", encoding="utf-8") if log_file else None

    try:
        async with websockets.connect(ws_url) as ws:
            # Enable Console and Runtime domains
            await ws.send(json.dumps({"id": 1, "method": "Console.enable"}))
            await ws.send(json.dumps({"id": 2, "method": "Runtime.enable"}))
            print("[+] Console and Runtime domains enabled. Streaming logs (Ctrl+C to stop)...")

            async for msg in ws:
                data = json.loads(msg)
                method = data.get("method")
                params = data.get("params", {})

                line = None
                if method == "Console.messageAdded":
                    message = params.get("message", {})
                    level = message.get("level", "log").upper()
                    text = message.get("text", "")
                    line = f"[{level}] {text}"
                elif method == "Runtime.consoleAPICalled":
                    call_type = params.get("type", "log").upper()
                    args = params.get("args", [])
                    vals = []
                    for arg in args:
                        if "value" in arg:
                            vals.append(str(arg["value"]))
                        elif "description" in arg:
                            vals.append(arg["description"])
                        else:
                            vals.append(json.dumps(arg))
                    line = f"[{call_type}] {' '.join(vals)}"
                elif method == "Runtime.exceptionThrown":
                    details = params.get("exceptionDetails", {})
                    text = details.get("text", "Exception")
                    exc = details.get("exception", {}).get("description", "")
                    line = f"[ERROR] {text}: {exc}"

                if line:
                    print(line)
                    if out_fh:
                        out_fh.write(line + "\n")
                        out_fh.flush()
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\n[*] Detaching from tab...")
    finally:
        if out_fh:
            out_fh.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream browser console logs via CDP.")
    parser.add_argument("--host", default="127.0.0.1", help="Debugging host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9222, help="Debugging port (default: 9222)")
    parser.add_argument("--filter", default="", help="Filter substring for tab title or URL (e.g. play-cs, assault)")
    parser.add_argument("--output", "-o", default=None, help="Optional log file path to write output")
    args = parser.parse_args()

    targets = find_targets(args.host, args.port, args.filter)
    if not targets:
        print(f"[-] No matching tabs found with filter: {args.filter!r}", file=sys.stderr)
        all_targets = find_targets(args.host, args.port, "")
        if all_targets:
            print("    Available tabs:")
            for t in all_targets:
                print(f"     - {t.get('title')} ({t.get('url')})")
        sys.exit(1)

    target = targets[0]
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        print("[-] Selected tab does not have webSocketDebuggerUrl.", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(stream_tab(ws_url, target.get("title", ""), args.output))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
