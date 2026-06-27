import asyncio
import json
import pytest
import httpx
from pathlib import Path

from backend.modules.agent import evaluator, orchestrator
from backend.modules.agent.models import AgentConfig

def _configure(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestrator,
        "_load_config",
        lambda: AgentConfig(model="m", endpoint="http://ollama.test"),
    )

def _mock_ollama(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        orchestrator,
        "instrumented_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

def test_run_evaluation_success(monkeypatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        if calls["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "files.write",
                                    "arguments": {
                                        "path": "main.py",
                                        "content": 'print("Hello World")'
                                    },
                                }
                            }
                        ],
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "Created the main.py script.",
                }
            },
        )

    _configure(monkeypatch)
    _mock_ollama(monkeypatch, handler)

    # Use a custom task for stable testing without running shell dependencies
    task = evaluator.EvaluationTask(
        id="test_task",
        name="Test Task",
        description="Verify file creation",
        prompt="Write print('Hello World') to main.py",
        expected_tools=["files.write"],
        state_validators=[
            evaluator.StateValidator(type="file_exists", target="main.py"),
            evaluator.StateValidator(type="file_contains", target="main.py", expected="Hello World"),
        ]
    )

    res = asyncio.run(evaluator.run_evaluation(task))
    assert res.success is True
    assert res.turns == 1
    assert "files.write" in res.tool_calls
    assert res.precision == 1.0
    assert res.recall == 1.0
    assert res.f1 == 1.0
    assert len(res.errors) == 0

def test_run_evaluation_fails_validation(monkeypatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "I failed to write anything.",
                }
            },
        )

    _configure(monkeypatch)
    _mock_ollama(monkeypatch, handler)

    task = evaluator.EvaluationTask(
        id="test_task_fail",
        name="Test Task Fail",
        description="Verify file creation failure",
        prompt="Write print('Hello World') to main.py",
        expected_tools=["files.write"],
        state_validators=[
            evaluator.StateValidator(type="file_exists", target="main.py"),
        ]
    )

    res = asyncio.run(evaluator.run_evaluation(task))
    assert res.success is False
    assert res.turns == 0
    assert res.precision == 0.0
    assert res.recall == 0.0
    assert res.f1 == 0.0
    assert len(res.errors) > 0
    assert any("Validation failed" in err for err in res.errors)

def test_run_evaluation_banned_tool(monkeypatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "terminal.exec",
                                "arguments": {"command": "echo 'Hello'"},
                            }
                        }
                    ],
                }
            },
        )

    _configure(monkeypatch)
    _mock_ollama(monkeypatch, handler)

    task = evaluator.EvaluationTask(
        id="test_task_banned",
        name="Test Task Banned",
        description="Verify banned tool detection",
        prompt="List files without terminal",
        banned_tools=["terminal.exec"],
        state_validators=[]
    )

    res = asyncio.run(evaluator.run_evaluation(task))
    assert res.success is False
    assert "terminal.exec" in res.tool_calls
    assert len(res.errors) > 0
    assert any("Banned tool called" in err for err in res.errors)


# Endpoint integration tests
from fastapi.testclient import TestClient
from backend.app import app

def test_list_eval_tasks_route() -> None:
    client = TestClient(app)
    res = client.get("/api/agent/eval/tasks")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert len(body) > 0
    task_ids = {t["id"] for t in body}
    assert "file_creation" in task_ids

def test_run_eval_task_not_found() -> None:
    client = TestClient(app)
    res = client.post("/api/agent/eval/run/nonexistent_task")
    assert res.status_code == 404

