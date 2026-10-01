"""Socket bridge to execute arbitrary Python code and scripts inside a running Blender 5.2 instance."""

import argparse
import json
import socket
import sys
from pathlib import Path


def exec_blender(code: str, host: str = "127.0.0.1", port: int = 9876, strict_json: bool = True, timeout: float = 60.0) -> dict:
    """Send Python code to Blender's MCP TCP socket server and return the parsed JSON result."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
    except ConnectionRefusedError:
        raise ConnectionRefusedError(
            f"Could not connect to Blender on {host}:{port}. Ensure Blender is running with the MCP bridge active."
        )

    req = {
        "type": "execute",
        "code": code,
        "strict_json": strict_json,
    }
    s.sendall(json.dumps(req).encode("utf-8") + b"\0")

    data = bytearray()
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
        if b"\0" in data:
            data = data.split(b"\0")[0]
            break
    s.close()
    if not data:
        raise RuntimeError("Received empty response from Blender socket server")
    return json.loads(data.decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Execute Python scripts or expressions in live Blender.")
    parser.add_argument("script", nargs="?", help="Path to Python script to execute in Blender")
    parser.add_argument("-c", "--code", help="Python code string to execute")
    parser.add_argument("--host", default="127.0.0.1", help="Blender MCP host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9876, help="Blender MCP port (default 9876)")
    parser.add_argument("--relaxed", action="store_true", help="Allow non-strict JSON return values")
    args = parser.parse_args()

    if args.code:
        code = args.code
    elif args.script:
        code = Path(args.script).read_text(encoding="utf-8")
    else:
        parser.error("Must provide either a script path or -c code")

    res = exec_blender(code, host=args.host, port=args.port, strict_json=not args.relaxed)
    print(json.dumps(res, indent=2))
    if res.get("status") != "ok":
        sys.exit(1)


if __name__ == "__main__":
    main()
