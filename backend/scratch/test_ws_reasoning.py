from fastapi.testclient import TestClient
from backend.app import app


def main():
    client = TestClient(app)
    print("Connecting to /ws via TestClient...")
    with client.websocket_connect("/ws") as ws:
        # Wait for system hello
        hello = ws.receive_json()
        print(f"Received greeting: {hello}")

        # Send ask message
        ask_msg = {
            "channel": "agent",
            "event": "ask",
            "data": {
                "turnId": "test-turn-1",
                "prompt": "think about the number 42 and say hello",
                "history": [],
                "context": {},
            },
        }
        print("Sending ask message...")
        ws.send_json(ask_msg)

        print("Waiting for responses...")
        reasoning_received = False
        tokens_received = False
        while True:
            try:
                msg = ws.receive_json()
                channel = msg.get("channel")
                event = msg.get("event")
                data = msg.get("data") or {}

                if channel == "agent":
                    if event == "reasoning":
                        reasoning_received = True
                        print(f"[REASONING] {repr(data.get('delta'))}")
                    elif event == "token":
                        tokens_received = True
                        print(f"[TOKEN] {repr(data.get('delta'))}")
                    elif event == "answer":
                        print(f"[ANSWER] {repr(data.get('text'))}")
                    elif event == "done":
                        print("[DONE]")
                        break
                    elif event == "error":
                        print(f"[ERROR] {repr(data.get('message'))}")
                        break
                    else:
                        print(f"[AGENT EVENT] {event}: {data}")
                else:
                    print(f"[EVENT] channel={channel} event={event}")
            except Exception as e:
                print(f"Connection closed or error: {e}")
                break

        print(f"Reasoning received: {reasoning_received}")
        print(f"Tokens received: {tokens_received}")


if __name__ == "__main__":
    main()
