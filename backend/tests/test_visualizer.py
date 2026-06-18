import pytest
import time
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.visualizer import visualizer_manager


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_pygame_visualization_flow(client: TestClient) -> None:
    # Make sure we clean up any active sessions
    visualizer_manager.close_all()

    with client.websocket_connect("/ws") as ws:
        # 1. Read hello message
        hello_msg = ws.receive_json()
        assert hello_msg["channel"] == "system"
        assert hello_msg["event"] == "hello"

        # 2. Start a simple Pygame visualization script
        pygame_code = """
import pygame
import time
pygame.init()
screen = pygame.display.set_mode((100, 100))
screen.fill((255, 0, 0))
pygame.display.flip()
# Sleep a bit to keep it alive
time.sleep(0.5)
"""
        ws.send_json({
            "channel": "visualizer",
            "event": "start_pygame",
            "data": {"code": pygame_code}
        })

        # 3. Read messages until we get a frame
        frame_received = False
        start_time = time.time()
        # Allow up to 5 seconds to get a frame
        while time.time() - start_time < 5.0:
            msg = ws.receive_json()
            if msg.get("channel") == "visualizer":
                if msg.get("event") == "frame":
                    assert "frame" in msg["data"]
                    assert msg["data"]["frame"].startswith("data:image/jpeg;base64,")
                    frame_received = True
                    break
                elif msg.get("event") == "error":
                    pytest.fail(f"Received unexpected visualizer error: {msg['data'].get('message')}")

        assert frame_received, "Failed to receive a visualizer frame from Pygame subprocess"

        # 4. Stop the Pygame visualization
        ws.send_json({
            "channel": "visualizer",
            "event": "stop_pygame"
        })

        # Give the backend a brief moment to process the stop event and terminate the process
        time.sleep(0.5)
        # Check that visualizer manager has stopped the process
        assert len(visualizer_manager.sessions) == 0


def test_pygame_visualization_error_handling(client: TestClient) -> None:
    visualizer_manager.close_all()

    with client.websocket_connect("/ws") as ws:
        hello_msg = ws.receive_json()
        assert hello_msg["channel"] == "system"

        # Start a script that raises ZeroDivisionError
        error_code = """
import pygame
pygame.init()
screen = pygame.display.set_mode((100, 100))
pygame.display.flip()
x = 1 / 0
"""
        ws.send_json({
            "channel": "visualizer",
            "event": "start_pygame",
            "data": {"code": error_code}
        })

        # Read messages until we get an error
        error_received = False
        start_time = time.time()
        while time.time() - start_time < 5.0:
            msg = ws.receive_json()
            if msg.get("channel") == "visualizer":
                if msg.get("event") == "error":
                    assert "message" in msg["data"]
                    assert "ZeroDivisionError" in msg["data"]["message"]
                    error_received = True
                    break

        assert error_received, "Failed to receive a visualizer error from faulty Pygame subprocess"
