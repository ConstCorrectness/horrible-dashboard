"""MongoDB driver + Atlas admin gate.

No live cluster is needed (or wanted) here: everything covered is the part that
decides *whether* and *how* a query reaches one — the read-only classification, the
query-body parser, the admin gate on the built-in `atlas` connection, and the promise
that the cluster URI never leaves the backend. The parts that need a server (a real
`find`) are exercised by hand against a cluster, the same posture as the
Chroma/Qdrant/Weaviate drivers.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import atlas
from backend.app import app
from backend.modules.database import connections as conn_store
from backend.modules.database.drivers import PROVIDERS, get_dialect, get_driver
from backend.modules.database.drivers import mongo_driver as md
from backend.modules.database.drivers.base import DriverError, query_is_read_only


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A node configured as a cluster admin, read-only."""
    monkeypatch.setenv("ATLAS_DB_USER", "svc")
    monkeypatch.setenv("ATLAS_DB_PASS", "p@ss:word/1")
    monkeypatch.setenv("ATLAS_CLUSTER_HOST", "horrible.abc12.mongodb.net")
    monkeypatch.delenv("ATLAS_DB_URI", raising=False)
    monkeypatch.delenv("ATLAS_ADMIN_URI", raising=False)
    monkeypatch.setenv("ATLAS_ADMIN", "1")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_mongodb_is_registered_with_the_mongo_dialect() -> None:
    assert get_driver("mongodb") is md
    assert get_dialect("mongodb") == "mongo"
    entry = next(e for e in PROVIDERS if e["id"] == "mongodb")
    assert entry["dialect"] == "mongo"
    # PROVIDERS drives the connection form; a field the driver ignores is a dead input.
    assert "uri" in entry["fields"]


# ---------------------------------------------------------------------------
# Read-only gating
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ('{"op": "find", "collection": "presence"}', True),
        ('{"op": "count", "collection": "presence"}', True),
        ('{"op": "collections"}', True),
        ('{"op": "databases"}', True),
        ('{"op": "indexes", "collection": "presence"}', True),
        ('{"op": "insert", "collection": "c", "documents": []}', False),
        ('{"op": "update", "collection": "c", "update": {}}', False),
        ('{"op": "delete", "collection": "c", "filter": {}}', False),
        ('{"op": "drop_collection", "collection": "c"}', False),
        # A raw database command can be anything, so it can't be vouched for.
        ('{"op": "command", "command": {"ping": 1}}', False),
        # Fail closed on anything unreadable.
        ("not json", False),
        ("{}", False),
        ('{"op": "find"', False),
        ('{"op": "brand_new_op", "collection": "c"}', False),
    ],
)
def test_mongo_read_only_gate(query: str, expected: bool) -> None:
    assert query_is_read_only(query, "mongo") is expected


def test_aggregate_is_read_only_only_without_out_or_merge() -> None:
    """The one op whose *name* doesn't settle it: `$out`/`$merge` rewrite a collection.

    Classifying `aggregate` as a read on the strength of the op name would let a
    read-only console (and the agent's read-only query tool) replace a collection on
    the shared cluster.
    """
    plain = json.dumps(
        {
            "op": "aggregate",
            "collection": "presence",
            "pipeline": [{"$group": {"_id": "$person_id", "n": {"$sum": 1}}}],
        }
    )
    assert query_is_read_only(plain, "mongo") is True

    for stage in ({"$out": "copy"}, {"$merge": {"into": "copy"}}):
        writing = json.dumps(
            {
                "op": "aggregate",
                "collection": "presence",
                "pipeline": [{"$match": {}}, stage],
            }
        )
        assert query_is_read_only(writing, "mongo") is False


def test_write_stages_are_found_at_any_depth() -> None:
    """`$out` is only *legal* as the final top-level stage — but a gate that only
    looks there is a gate that trusts input to be well-formed."""
    nested = json.dumps(
        {
            "op": "aggregate",
            "collection": "c",
            "pipeline": [{"$facet": {"a": [{"$merge": {"into": "x"}}]}}],
        }
    )
    assert query_is_read_only(nested, "mongo") is False


# ---------------------------------------------------------------------------
# Query body
# ---------------------------------------------------------------------------


def test_parse_query_reads_the_whole_body() -> None:
    q = md.parse_query(
        json.dumps(
            {
                "op": "find",
                "collection": "presence",
                "db": "other",
                "filter": {"person_id": "abc"},
                "projection": {"addresses": 1},
                "sort": {"ts": -1},
                "limit": 5,
                "skip": 10,
            }
        )
    )
    assert q.op == "find"
    assert q.collection == "presence"
    assert q.db == "other"
    assert q.filter == {"person_id": "abc"}
    assert q.projection == {"addresses": 1}
    assert q.limit == 5
    assert q.skip == 10
    assert md._sort_spec(q.sort) == [("ts", -1)]


def test_extended_json_types_are_parsed() -> None:
    """`{"$oid": …}` must become a real ObjectId — coercing bare hex strings instead
    would silently change the meaning of a filter on a string field."""
    from bson import ObjectId

    q = md.parse_query(
        '{"op": "find", "collection": "c", '
        '"filter": {"_id": {"$oid": "65f0000000000000000000aa"}}}'
    )
    assert isinstance(q.filter["_id"], ObjectId)


def test_multi_key_sort_keeps_the_order_it_was_written_in() -> None:
    q = md.parse_query(
        '{"op": "find", "collection": "c", "sort": {"ts": -1, "name": 1}}'
    )
    assert md._sort_spec(q.sort) == [("ts", -1), ("name", 1)]


@pytest.mark.parametrize(
    "body",
    [
        "",
        "not json",
        '["op"]',
        '{"collection": "c"}',  # no op
        '{"op": "nope"}',
        '{"op": "find", "collection": "c", "filter": []}',
        '{"op": "aggregate", "collection": "c", "pipeline": {}}',
        '{"op": "insert", "collection": "c", "documents": [1]}',
        '{"op": "find", "collection": "c", "limit": 0}',
        '{"op": "find", "collection": "c", "skip": -1}',
    ],
)
def test_parse_query_rejects_bad_bodies(body: str) -> None:
    with pytest.raises(DriverError):
        md.parse_query(body)


def test_parse_errors_name_the_provider_and_show_an_example() -> None:
    with pytest.raises(DriverError) as exc:
        md.parse_query("{oops}")
    assert "mongodb" in str(exc.value)
    assert '"op"' in str(exc.value)


def test_collection_is_required_where_it_matters() -> None:
    q = md.parse_query('{"op": "find"}')
    with pytest.raises(DriverError, match="collection"):
        q.require_collection()


def test_sort_direction_must_be_one_or_minus_one() -> None:
    q = md.parse_query('{"op": "find", "collection": "c", "sort": {"ts": "up"}}')
    with pytest.raises(DriverError, match="sort direction"):
        md._sort_spec(q.sort)


# ---------------------------------------------------------------------------
# URI building
# ---------------------------------------------------------------------------


def test_uri_percent_encodes_credentials() -> None:
    """Mongo passwords routinely contain URI-structural characters; not encoding them
    either errors or connects somewhere unintended."""
    uri = md._uri(
        {"host": "db.local", "user": "a:b", "password": "p@ss/word", "tls": True}
    )
    assert "a%3Ab:p%40ss%2Fword@db.local:27017" in uri
    assert "tls=true" in uri


def test_explicit_uri_wins() -> None:
    assert md._uri({"uri": "mongodb+srv://x/", "host": "ignored"}) == "mongodb+srv://x/"


def test_uri_needs_something_to_dial() -> None:
    with pytest.raises(DriverError, match="uri"):
        md._uri({})


# ---------------------------------------------------------------------------
# Connection-level read-only enforcement
# ---------------------------------------------------------------------------


def test_read_only_connection_refuses_writes_whatever_the_caller_asks() -> None:
    """The gate has to live in the driver: `run_query(read_only=False)` is what the
    agent's `database.execute` tool sends, so a route-only check is a back door."""
    config = {"uri": "mongodb://localhost", "read_only": True, "builtin": True}
    q = md.parse_query('{"op": "delete", "collection": "c", "filter": {}}')
    with pytest.raises(DriverError, match="read-only"):
        md._guard_writes(
            config, q, '{"op": "delete", "collection": "c", "filter": {}}', False
        )


def test_read_only_connection_hint_mentions_the_env_switch() -> None:
    config = {"uri": "mongodb://localhost", "read_only": True, "builtin": True}
    body = '{"op": "drop_collection", "collection": "c"}'
    with pytest.raises(DriverError, match="ATLAS_ADMIN=rw"):
        md._guard_writes(config, md.parse_query(body), body, False)


def test_writable_connection_allows_a_write() -> None:
    config = {"uri": "mongodb://localhost", "read_only": False}
    body = '{"op": "insert", "collection": "c", "documents": [{"a": 1}]}'
    md._guard_writes(config, md.parse_query(body), body, False)  # no raise


def test_request_read_only_still_refuses_a_write_on_a_writable_connection() -> None:
    config = {"uri": "mongodb://localhost"}
    body = '{"op": "insert", "collection": "c", "documents": []}'
    with pytest.raises(DriverError, match="read-only"):
        md._guard_writes(config, md.parse_query(body), body, True)


# ---------------------------------------------------------------------------
# Results grid
# ---------------------------------------------------------------------------


def test_documents_flatten_with_id_first_and_ragged_keys_unioned() -> None:
    import time

    from bson import ObjectId

    oid = ObjectId("65f0000000000000000000aa")
    result = md._documents_to_result(
        [
            {"name": "a", "_id": oid, "kind": "note"},
            {"_id": oid, "extra": 1},
        ],
        started=time.perf_counter(),
        row_limit=10,
    )
    names = [c.name for c in result.columns]
    assert names[0] == "_id"
    assert set(names) == {"_id", "name", "kind", "extra"}
    # ObjectId renders as bare hex: readable in a grid, and typed back as {"$oid": …}.
    assert result.rows[0][0] == "65f0000000000000000000aa"
    assert result.rows[1][names.index("name")] is None


def test_documents_flag_truncation() -> None:
    import time

    result = md._documents_to_result(
        [{"i": i} for i in range(4)], started=time.perf_counter(), row_limit=3
    )
    assert result.rowcount == 3
    assert result.truncated is True


# ---------------------------------------------------------------------------
# The Atlas admin gate
# ---------------------------------------------------------------------------


def test_atlas_connection_is_absent_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every node on the fabric holds cluster credentials to publish its presence
    record. If that were the gate, every user would get a console over shared
    infrastructure."""
    monkeypatch.setenv("ATLAS_DB_USER", "svc")
    monkeypatch.setenv("ATLAS_DB_PASS", "pw")
    monkeypatch.setenv("ATLAS_CLUSTER_HOST", "horrible.abc12.mongodb.net")
    monkeypatch.delenv("ATLAS_ADMIN", raising=False)

    assert atlas.is_configured() is True
    assert atlas.admin_access() == "off"
    assert conn_store.get_connection("atlas") is None


def test_atlas_admin_flag_is_ignored_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ATLAS_DB_URI", raising=False)
    monkeypatch.delenv("ATLAS_ADMIN_URI", raising=False)
    monkeypatch.delenv("ATLAS_CLUSTER_HOST", raising=False)
    monkeypatch.setenv("ATLAS_ADMIN", "rw")
    assert atlas.admin_access() == "off"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", "ro"),
        ("true", "ro"),
        ("ro", "ro"),
        ("read-only", "ro"),
        ("rw", "rw"),
        ("admin", "rw"),
        ("0", "off"),
        ("maybe", "off"),
    ],
)
def test_admin_access_levels(
    admin_env: None, monkeypatch: pytest.MonkeyPatch, value: str, expected: str
) -> None:
    monkeypatch.setenv("ATLAS_ADMIN", value)
    assert atlas.admin_access() == expected


def test_atlas_connection_appears_for_an_admin_and_is_read_only(
    admin_env: None,
) -> None:
    conn = conn_store.get_connection("atlas")
    assert conn is not None
    assert conn["provider"] == "mongodb"
    assert conn["builtin"] is True
    assert conn["config"]["read_only"] is True
    # The listed config must carry no URI at all — see the redaction test below.
    assert "uri" not in conn["config"]

    resolved = conn_store.resolve_config(conn)
    assert resolved["uri"].startswith("mongodb+srv://svc:")
    assert resolved["read_only"] is True


def test_atlas_connection_is_writable_under_rw(
    admin_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_ADMIN", "rw")
    conn = conn_store.get_connection("atlas")
    assert conn is not None
    assert conn["config"]["read_only"] is False


def test_admin_uri_can_point_at_a_higher_privileged_user(
    admin_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_ADMIN_URI", "mongodb+srv://root:pw@other.host/")
    conn = conn_store.get_connection("atlas")
    assert conn is not None
    assert conn_store.resolve_config(conn)["uri"] == "mongodb+srv://root:pw@other.host/"
    assert conn["name"].endswith("(other.host)")


def test_resolve_config_fails_closed_if_admin_access_is_revoked(
    admin_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = conn_store.get_connection("atlas")
    assert conn is not None
    monkeypatch.delenv("ATLAS_ADMIN")
    with pytest.raises(PermissionError):
        conn_store.resolve_config(conn)


def test_atlas_connection_cannot_be_edited_or_deleted(
    admin_env: None, client: TestClient
) -> None:
    assert client.delete("/api/database/connections/atlas").status_code == 400
    assert (
        client.put(
            "/api/database/connections/atlas",
            json={"name": "x", "provider": "mongodb", "config": {}},
        ).status_code
        == 400
    )


# ---------------------------------------------------------------------------
# Secret handling
# ---------------------------------------------------------------------------


def test_connection_uri_is_never_returned_to_the_client(client: TestClient) -> None:
    """A connection string embeds the password, so `uri` has to be redacted like one.
    A redactor that only knew about `password` would hand it to any page that asked."""
    created = client.post(
        "/api/database/connections",
        json={
            "name": "my mongo",
            "provider": "mongodb",
            "config": {"uri": "mongodb+srv://user:sup3rsecret@host/", "database": "d"},
        },
    )
    assert created.status_code == 200
    conn_id = created.json()["id"]
    try:
        assert created.json()["config"]["uri"] is True  # redacted to "is set"
        body = client.get("/api/database/connections").text
        assert "sup3rsecret" not in body
    finally:
        client.delete(f"/api/database/connections/{conn_id}")


def test_atlas_password_is_not_in_the_connections_response(
    admin_env: None, client: TestClient
) -> None:
    res = client.get("/api/database/connections")
    assert res.status_code == 200
    assert "p@ss:word/1" not in res.text
    assert "p%40ss%3Aword%2F1" not in res.text  # nor percent-encoded inside a URI
    atlas_conn = next(c for c in res.json()["connections"] if c["id"] == "atlas")
    assert atlas_conn["dialect"] == "mongo"


def test_mongo_read_only_rejection_explains_the_mongo_ops(
    admin_env: None, client: TestClient
) -> None:
    """The 400 has to speak MQL — quoting the vector ops at a Mongo connection is how
    a user (or an agent) ends up rewriting a working query into a wrong one."""
    res = client.post(
        "/api/database/query",
        json={
            "connection_id": "atlas",
            "sql": '{"op": "drop_collection", "collection": "presence"}',
            "read_only": True,
        },
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "find" in detail and "aggregate" in detail
    assert "$out" in detail
