import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.modules.database.vectorstore import init_db

    init_db()
    return TestClient(app)


def test_status_endpoint(client: TestClient) -> None:
    res = client.get("/api/database/status")
    assert res.status_code == 200
    data = res.json()
    assert "db_path" in data
    assert data["num_documents"] == 0
    assert len(data["collections"]) == 0


def test_upsert_and_list_documents(client: TestClient) -> None:
    # Insert a document
    doc_payload = {
        "id": "doc1",
        "collection": "test_settings",
        "text": "The agent should always be polite and help with coding.",
        "metadata": {"source": "user_settings", "category": "agent_rules"},
    }
    res = client.post("/api/database/documents", json=doc_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == "doc1"
    assert data["collection"] == "test_settings"
    assert data["text"] == doc_payload["text"]
    assert data["metadata"]["source"] == "user_settings"
    assert "_embedding_source" in data["metadata"]

    # Verify status reflects 1 document
    status_res = client.get("/api/database/status").json()
    assert status_res["num_documents"] == 1
    assert len(status_res["collections"]) == 1
    assert status_res["collections"][0]["name"] == "test_settings"
    assert status_res["collections"][0]["count"] == 1

    # List documents
    list_res = client.get("/api/database/documents?collection=test_settings")
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["total"] == 1
    assert len(list_data["documents"]) == 1
    assert list_data["documents"][0]["id"] == "doc1"


def test_semantic_search(client: TestClient) -> None:
    # Insert two documents with distinct topics
    client.post(
        "/api/database/documents",
        json={
            "id": "doc_agent",
            "collection": "memories",
            "text": "agent memory and orchestrator rules for running shell commands",
            "metadata": {"type": "agent"},
        },
    )
    client.post(
        "/api/database/documents",
        json={
            "id": "doc_ui",
            "collection": "memories",
            "text": "user interface color themes and responsive window sizes",
            "metadata": {"type": "ui"},
        },
    )

    # Perform search for ui topic
    search_payload = {
        "text": "responsive themes window layout",
        "collection": "memories",
        "limit": 5,
    }
    res = client.post("/api/database/search", json=search_payload)
    assert res.status_code == 200
    results = res.json()
    assert len(results) == 2
    # The first result should be the UI document since it shares words like "responsive", "window", "themes/theme"
    assert results[0]["id"] == "doc_ui"
    assert results[0]["score"] > results[1]["score"]


def test_delete_documents(client: TestClient) -> None:
    # Insert document
    client.post(
        "/api/database/documents",
        json={"id": "to_delete", "collection": "trash", "text": "temporary dump"},
    )

    # Verify count is 1
    assert client.get("/api/database/status").json()["num_documents"] == 1

    # Delete
    del_res = client.delete("/api/database/documents/to_delete")
    assert del_res.status_code == 200
    assert del_res.json() == {"deleted": True, "id": "to_delete"}

    # Verify count is 0
    assert client.get("/api/database/status").json()["num_documents"] == 0

    # Delete non-existent raises 404
    err_res = client.delete("/api/database/documents/to_delete")
    assert err_res.status_code == 404


def test_semantic_search_intents(client: TestClient) -> None:
    # Insert multiple utterances mapping to the same intent, and some mapping to another
    client.post(
        "/api/database/documents",
        json={
            "id": "utt1",
            "collection": "intents_test",
            "text": "what is the temperature today",
            "metadata": {
                "intent": "get_weather",
                "namespace": "weather",
                "full_intent": "weather/get_weather",
            },
        },
    )
    client.post(
        "/api/database/documents",
        json={
            "id": "utt2",
            "collection": "intents_test",
            "text": "is it raining outside",
            "metadata": {
                "intent": "get_weather",
                "namespace": "weather",
                "full_intent": "weather/get_weather",
            },
        },
    )
    client.post(
        "/api/database/documents",
        json={
            "id": "utt3",
            "collection": "intents_test",
            "text": "play some music please",
            "metadata": {
                "intent": "play_music",
                "namespace": "media",
                "full_intent": "media/play_music",
            },
        },
    )

    # Perform search
    search_payload = {
        "text": "is it hot or raining today?",
        "collection": "intents_test",
        "limit": 5,
    }
    res = client.post("/api/database/search", json=search_payload)
    assert res.status_code == 200
    results = res.json()

    # We should have grouped by intent, so we expect only 2 unique intents in results, not 3 utterances
    assert len(results) == 2

    # Check that text is the full_intent, not the utterances
    assert results[0]["text"] == "weather/get_weather"
    assert results[1]["text"] == "media/play_music"

    # The score should be the highest of the grouped utterances
    assert results[0]["score"] > results[1]["score"]


# ---------------------------------------------------------------------------
# Generic inspector: connections, query, schema
# ---------------------------------------------------------------------------


def test_connections_lists_builtin_app(client: TestClient) -> None:
    res = client.get("/api/database/connections")
    assert res.status_code == 200
    data = res.json()
    ids = [c["id"] for c in data["connections"]]
    assert "app" in ids
    app_conn = next(c for c in data["connections"] if c["id"] == "app")
    assert app_conn["builtin"] is True
    assert app_conn["provider"] == "sqlite"
    assert {p["id"] for p in data["providers"]} >= {
        "sqlite",
        "postgres",
        "duckdb",
        "mysql",
    }


def test_builtin_app_connection_opens_the_app_db_not_the_vector_store(
    client: TestClient, tmp_path
) -> None:
    """The `app` connection must point at `app.db`, not the LanceDB directory.

    Regression: `connections.py` imported `get_db_path` from `vectorstore`, which after
    the LanceDB migration returns a *directory*. Listing the connection still worked —
    only opening it failed, with sqlite3's opaque "unable to open database file". So
    assert the connection can actually be *used*, not merely enumerated.
    """
    from backend.modules.database.connections import get_connection, resolve_config

    cfg = resolve_config(get_connection("app"))
    assert cfg["path"] == str(tmp_path / "app.db")

    res = client.get("/api/database/connections/app/schema")
    assert res.status_code == 200, res.text


def test_connection_crud_and_secret_redaction(client: TestClient, tmp_path) -> None:
    payload = {
        "name": "My PG",
        "provider": "postgres",
        "config": {"host": "localhost", "user": "me", "password": "s3cret"},
    }
    created = client.post("/api/database/connections", json=payload).json()
    assert created["name"] == "My PG"
    # Password must never be echoed back; it is redacted to a boolean.
    assert created["config"]["password"] is True

    conn_id = created["id"]
    listed = client.get("/api/database/connections").json()["connections"]
    assert any(c["id"] == conn_id for c in listed)

    deleted = client.delete(f"/api/database/connections/{conn_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_builtin_connection_is_read_only(client: TestClient) -> None:
    assert client.delete("/api/database/connections/app").status_code == 400
    assert (
        client.put(
            "/api/database/connections/app",
            json={"name": "x", "provider": "sqlite", "config": {}},
        ).status_code
        == 400
    )
