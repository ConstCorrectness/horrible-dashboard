"""Notebook module: REST catalog/create/mode routes (TestClient) plus a ws kernel
smoke test that spawns a REAL ipykernel from this test process's python (which has
ipykernel as a dev dep), exercising open → edit → run → output over the `notebook`
channel — the notebook-side counterpart to test_training_kernels.py."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def nb_root(tmp_path, monkeypatch) -> Path:
    """A notebook root under a temp data dir, wired via the settings file."""
    import os

    data_dir = Path(os.environ["HORRIBLE_DATA_DIR"])
    root = tmp_path / "notebooks"
    root.mkdir()
    (data_dir / "settings.json").write_text(json.dumps({"notebook.root": str(root)}))
    return root


@pytest.fixture
def client(nb_root) -> TestClient:
    from backend.app import app

    return TestClient(app)


def test_create_list_doc_and_mode(client: TestClient) -> None:
    # Create.
    res = client.post(
        "/api/notebook", json={"path": "explore.ipynb", "mode": "reactive"}
    )
    assert res.status_code == 200, res.text
    doc = res.json()
    assert doc["path"] == "explore.ipynb"
    assert doc["metadata"]["horrible"]["execution_mode"] == "reactive"
    assert len(doc["cells"]) == 2  # starter markdown + code

    # List.
    files = client.get("/api/notebook/files").json()["files"]
    assert [f["path"] for f in files] == ["explore.ipynb"]

    # Load doc.
    got = client.get("/api/notebook/doc", params={"path": "explore.ipynb"}).json()
    assert got["path"] == "explore.ipynb"

    # Toggle mode.
    updated = client.put(
        "/api/notebook/mode", json={"path": "explore.ipynb", "mode": "classic"}
    ).json()
    assert updated["metadata"]["horrible"]["execution_mode"] == "classic"


def test_create_rejects_duplicate_and_escape(client: TestClient) -> None:
    client.post("/api/notebook", json={"path": "dup.ipynb"})
    assert client.post("/api/notebook", json={"path": "dup.ipynb"}).status_code == 409
    # Path traversal is refused by the escape-guarded resolver.
    assert (
        client.post("/api/notebook", json={"path": "../evil.ipynb"}).status_code == 400
    )


def test_doc_missing_is_404(client: TestClient) -> None:
    assert (
        client.get("/api/notebook/doc", params={"path": "nope.ipynb"}).status_code
        == 404
    )


class FakeConn:
    """Mirrors WsConnection.send_json (collects every notebook event)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self, event: str) -> list[dict[str, Any]]:
        return [s["data"] for s in self.sent if s.get("event") == event]


async def _wait(predicate, timeout: float = 60.0, what: str = "condition"):
    async def poll():
        while True:
            found = predicate()
            if found:
                return found
            await asyncio.sleep(0.05)

    try:
        return await asyncio.wait_for(poll(), timeout)
    except TimeoutError:
        raise AssertionError(f"timed out waiting for {what}") from None


def test_open_missing_notebook_errors(nb_root, monkeypatch) -> None:
    """Opening a path with no file on disk surfaces an error (no kernel spawn)."""
    from backend.modules.notebook.manager import NotebookManager

    async def go() -> None:
        mgr = NotebookManager()
        conn = FakeConn()
        await mgr.handle(conn, {"event": "open", "data": {"path": "ghost.ipynb"}})
        errs = await _wait(lambda: conn.events("error"), 10, "open error")
        assert errs[0]["sessionKey"] == "nb:ghost.ipynb"
        assert "not found" in errs[0]["message"]

    asyncio.run(go())


def test_reactive_cascade_and_stale_delete(nb_root, monkeypatch) -> None:
    """Reactive mode: running an upstream cell re-runs its dependents in order;
    deleting the upstream cell drops its var so a dependent raises NameError."""
    from backend.modules.notebook import env
    from backend.modules.notebook.manager import NotebookManager
    from backend.notebook_core import notebooks

    monkeypatch.setattr(env, "ensure_python", lambda progress=None: sys.executable)
    notebooks.new_notebook(
        nb_root / "r.ipynb",
        [
            {"cell_type": "code", "source": "a = 1"},
            {"cell_type": "code", "source": "b = a + 1"},
            {"cell_type": "code", "source": "print(b)"},
        ],
        metadata={"horrible": {"execution_mode": "reactive"}},
    )

    async def go() -> None:
        mgr = NotebookManager()
        conn = FakeConn()
        try:
            await mgr.handle(conn, {"event": "open", "data": {"path": "r.ipynb"}})
            opened = (await _wait(lambda: conn.events("opened"), 90, "kernel start"))[0]
            key = opened["sessionKey"]
            assert opened["mode"] == "reactive"
            ids = [c["id"] for c in opened["notebook"]["cells"]]
            a_id, b_id, print_id = ids

            # A `graph` event describes the dependency DAG a -> b -> print.
            graph = (await _wait(lambda: conn.events("graph"), 10, "graph"))[0]
            edge_set = {(e["from"], e["to"]) for e in graph["edges"]}
            assert (a_id, b_id) in edge_set and (b_id, print_id) in edge_set

            # Running the upstream cell cascades to both dependents.
            await mgr.handle(
                conn, {"event": "run_cell", "data": {"sessionKey": key, "cellId": a_id}}
            )
            await _wait(
                lambda: (
                    {
                        s["cellId"]
                        for s in conn.events("execution_state")
                        if s["state"] == "done"
                    }
                    >= {a_id, b_id, print_id}
                ),
                60,
                "cascade of 3 cells done",
            )
            printed = "".join(
                o["output"]["text"]
                for o in conn.events("output")
                if o["cellId"] == print_id and o["output"] and "text" in o["output"]
            )
            assert "2" in printed

            # Edit the upstream value and re-run → dependents recompute to 6.
            conn.sent.clear()
            await mgr.handle(
                conn,
                {
                    "event": "cells",
                    "data": {
                        "sessionKey": key,
                        "ops": [{"op": "edit", "cellId": a_id, "source": "a = 5"}],
                    },
                },
            )
            await mgr.handle(
                conn, {"event": "run_cell", "data": {"sessionKey": key, "cellId": a_id}}
            )
            await _wait(
                lambda: [
                    s
                    for s in conn.events("execution_state")
                    if s["cellId"] == print_id and s["state"] == "done"
                ],
                60,
                "re-run print done",
            )
            printed2 = "".join(
                o["output"]["text"]
                for o in conn.events("output")
                if o["cellId"] == print_id and o["output"] and "text" in o["output"]
            )
            assert "6" in printed2

            # Delete the upstream cell → `a` is stale; the dependent that reads it errors.
            conn.sent.clear()
            await mgr.handle(
                conn,
                {
                    "event": "cells",
                    "data": {
                        "sessionKey": key,
                        "ops": [{"op": "delete", "cellId": a_id}],
                    },
                },
            )
            await _wait(
                lambda: [
                    s
                    for s in conn.events("execution_state")
                    if s["cellId"] == b_id and s["state"] == "error"
                ],
                60,
                "stale dependent errors",
            )
            err = [
                o["output"]
                for o in conn.events("output")
                if o["cellId"] == b_id and o["output"] and o["output"].get("ename")
            ]
            assert err and err[0]["ename"] == "NameError"
        finally:
            await mgr.shutdown_all()

    asyncio.run(go())


def test_notebook_kernel_end_to_end(nb_root, monkeypatch) -> None:
    """open → edit → run a real kernel over the notebook channel."""
    from backend.modules.notebook import env
    from backend.modules.notebook.manager import NotebookManager
    from backend.notebook_core import notebooks

    # Use this test process's python (has ipykernel) instead of bootstrapping uv.
    monkeypatch.setattr(env, "ensure_python", lambda progress=None: sys.executable)
    notebooks.new_notebook(
        nb_root / "main.ipynb", [{"cell_type": "code", "source": "x = 1"}]
    )

    async def go() -> None:
        mgr = NotebookManager()
        conn = FakeConn()
        try:
            await mgr.handle(conn, {"event": "open", "data": {"path": "main.ipynb"}})
            opened = (await _wait(lambda: conn.events("opened"), 90, "kernel start"))[0]
            key = opened["sessionKey"]
            assert key == "nb:main.ipynb"
            assert opened["path"] == "main.ipynb"
            cid = opened["notebook"]["cells"][0]["id"]

            await mgr.handle(
                conn,
                {
                    "event": "cells",
                    "data": {
                        "sessionKey": key,
                        "ops": [
                            {"op": "edit", "cellId": cid, "source": "print('hello')"}
                        ],
                    },
                },
            )
            await mgr.handle(
                conn, {"event": "run_cell", "data": {"sessionKey": key, "cellId": cid}}
            )
            await _wait(
                lambda: [
                    s
                    for s in conn.events("execution_state")
                    if s["cellId"] == cid and s["state"] == "done"
                ],
                60,
                "cell done",
            )
            text = "".join(
                o["output"]["text"]
                for o in conn.events("output")
                if o["cellId"] == cid
                and o["output"]
                and o["output"]["output_type"] == "stream"
            )
            assert "hello" in text
        finally:
            await mgr.shutdown_all()

    asyncio.run(go())
