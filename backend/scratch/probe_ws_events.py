from fastapi.testclient import TestClient
from backend.app import app


def main():
    client = TestClient(app)
    print("Connecting to /ws via TestClient...")
    with client.websocket_connect("/ws") as ws:
        # Receive hello message
        print("Greeting: ", ws.receive_json())

        # In a real conversation, the client sends manifest first. Let's do that.
        manifest_msg = {"channel": "agent", "event": "manifest", "data": {"tools": []}}
        ws.send_json(manifest_msg)

        # Send ask message
        ask_msg = {
            "channel": "agent",
            "event": "ask",
            "data": {
                "turnId": "test-ws-reasoning-probe-1",
                "prompt": "Think about the number 42 as hard as you can",
                "history": [],
                "context": {},
            },
        }
        print("Sending ask...")
        ws.send_json(ask_msg)

        while True:
            try:
                msg = ws.receive_json()
                event = msg.get("event")
                data = msg.get("data") or {}
                if event == "reasoning":
                    print(f"[REASONING EVENT] delta={repr(data.get('delta'))}")
                elif event == "token":
                    print(f"[TOKEN EVENT] delta={repr(data.get('delta'))}")
                elif event == "answer":
                    print(f"[ANSWER EVENT] text={repr(data.get('text'))}")
                elif event == "done":
                    print("[DONE EVENT]")
                    break
                elif event == "error":
                    print(f"[ERROR EVENT] {repr(data.get('message'))}")
                    break
                else:
                    print(f"[OTHER EVENT] {event}")
            except Exception as e:
                print(f"Exception: {e}")
                break


if __name__ == "__main__":
    main()
