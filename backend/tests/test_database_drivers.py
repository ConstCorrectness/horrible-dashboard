"""Driver-layer tests: the dialect split, the vector query contract, and the
LanceDB driver against a real on-disk store.

Chroma/Qdrant/Weaviate/Oracle need their optional extras and (mostly) a running
server, so those drivers are only covered here for the parts that don't: their
registration, dialect, and lazy-import error message.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.database.drivers import PROVIDERS, get_dialect, get_driver
from backend.modules.database.drivers.base import (
    DriverError,
    jsonable,
    query_is_read_only,
)
from backend.modules.database.drivers.vector_base import (
    flatten_record,
    parse_query,
    records_to_result,
    similarity_from_distance,
)


# ---------------------------------------------------------------------------
# Registration + dialect
# ---------------------------------------------------------------------------


def test_every_provider_resolves_and_agrees_on_dialect() -> None:
    """PROVIDERS drives the UI; a mismatch there sends the wrong editor to a pane."""
    for entry in PROVIDERS:
        driver = get_driver(str(entry["id"]))
        assert driver.dialect == entry["dialect"], entry["id"]
        assert get_dialect(str(entry["id"])) == entry["dialect"]


def test_vector_providers_are_json_and_sql_providers_are_sql() -> None:
    dialects = {str(e["id"]): e["dialect"] for e in PROVIDERS}
    assert dialects["lancedb"] == "json"
    assert dialects["chroma"] == "json"
    assert dialects["qdrant"] == "json"
    assert dialects["weaviate"] == "json"
    assert dialects["oracle"] == "sql"
    assert dialects["sqlite"] == "sql"


def test_unknown_provider_raises() -> None:
    with pytest.raises(DriverError):
        get_driver("nope")


# ---------------------------------------------------------------------------
# Read-only gating
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "dialect", "expected"),
    [
        ("SELECT 1", "sql", True),
        ("  select * from t  ", "sql", True),
        ("DROP TABLE t", "sql", False),
        ("SELECT 1; DROP TABLE t", "sql", False),
        ('{"op": "search", "collection": "c"}', "json", True),
        ('{"op": "collections"}', "json", True),
        ('{"op": "delete", "ids": ["a"]}', "json", False),
        ('{"op": "drop_collection", "collection": "c"}', "json", False),
        # Unparseable or op-less bodies must fail closed, not open.
        ("not json at all", "json", False),
        ("{}", "json", False),
        ('["op"]', "json", False),
        ('{"op": "something_new"}', "json", False),
    ],
)
def test_read_only_gate(query: str, dialect: str, expected: bool) -> None:
    assert query_is_read_only(query, dialect) is expected


# ---------------------------------------------------------------------------
# Vector query contract
# ---------------------------------------------------------------------------


def test_parse_query_reads_the_whole_body() -> None:
    q = parse_query(
        json.dumps(
            {
                "op": "search",
                "collection": "lib",
                "query": "hello",
                "where": {"kind": "note"},
                "limit": 7,
                "offset": 3,
                "select": ["id", "text"],
                "metric": "l2",
            }
        ),
        provider="test",
    )
    assert (q.op, q.collection, q.query) == ("search", "lib", "hello")
    assert q.where == {"kind": "note"}
    assert (q.limit, q.offset, q.metric) == (7, 3, "l2")
    assert q.select == ["id", "text"]
    assert q.is_read_only


@pytest.mark.parametrize(
    "body",
    [
        "",
        "not json",
        "[1, 2]",
        "{}",
        '{"op": "frobnicate"}',
        '{"op": "search", "limit": 0}',
        '{"op": "search", "offset": -1}',
        '{"op": "search", "vector": ["a"]}',
        '{"op": "search", "where": "kind=note"}',
        '{"op": "upsert", "documents": [1]}',
    ],
)
def test_parse_query_rejects_bad_bodies(body: str) -> None:
    with pytest.raises(DriverError):
        parse_query(body, provider="test")


def test_parse_query_errors_name_the_provider_and_show_an_example() -> None:
    """These messages are the entire UX of a hand-typed JSON surface."""
    with pytest.raises(DriverError) as exc:
        parse_query("{", provider="chroma")
    message = str(exc.value)
    assert "chroma" in message
    assert '"op"' in message


def test_require_collection_is_enforced() -> None:
    q = parse_query('{"op": "search", "query": "x"}', provider="test")
    with pytest.raises(DriverError, match="collection"):
        q.require_collection("test")


# ---------------------------------------------------------------------------
# Result flattening
# ---------------------------------------------------------------------------


def test_records_to_result_unions_ragged_keys_and_orders_columns() -> None:
    records = [
        flatten_record(doc_id="a", text="hi", metadata={"kind": "note"}, score=0.9),
        flatten_record(doc_id="b", text="yo", metadata={"url": "u"}),
    ]
    result = records_to_result(records, started=0.0, row_limit=10)
    names = [c.name for c in result.columns]
    # id/score/text lead; metadata keys follow; every row is padded to the union.
    assert names[:3] == ["id", "score", "text"]
    assert set(names[3:]) == {"kind", "url"}
    assert all(len(row) == len(names) for row in result.rows)
    assert result.rowcount == 2


def test_flatten_record_does_not_let_metadata_shadow_reserved_columns() -> None:
    """A metadata key named `id` must not overwrite the document id."""
    record = flatten_record(doc_id="real", text="t", metadata={"id": "meta", "x": 1})
    assert record["id"] == "real"
    assert record["meta.id"] == "meta"
    assert record["x"] == 1


def test_records_to_result_flags_truncation() -> None:
    records = [flatten_record(doc_id=str(i)) for i in range(5)]
    result = records_to_result(records, started=0.0, row_limit=3)
    assert result.truncated is True
    assert result.rowcount == 3


def test_records_to_result_handles_no_rows() -> None:
    result = records_to_result([], started=0.0, row_limit=10)
    assert result.rowcount == 0
    assert result.columns == []


# ---------------------------------------------------------------------------
# Similarity conversion — the metric trap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("distance", "space", "expected"),
    [
        (0.25, "cosine", 0.75),
        (0.25, "COSINE", 0.75),
        # L2 is unbounded: 1 - d would yield a negative "score" that still sorts
        # plausibly, which is worse than showing no score at all.
        (1.87, "l2", None),
        (1.87, "euclidean", None),
        (0.5, None, None),
        (None, "cosine", None),
    ],
)
def test_similarity_only_derived_for_cosine(
    distance: float | None, space: str | None, expected: float | None
) -> None:
    assert similarity_from_distance(distance, space) == expected


def test_vector_cells_are_summarized_not_dumped() -> None:
    """Oracle 23ai VECTOR columns arrive as array.array; embeddings must not flood
    the grid."""
    import array

    assert jsonable(array.array("f", [0.1] * 768)) == "[768 numbers]"
    assert jsonable([0.1, 0.2]) == [0.1, 0.2]


# ---------------------------------------------------------------------------
# LanceDB driver against a real store
# ---------------------------------------------------------------------------


@pytest.fixture
def lance_store(tmp_path):
    """A small real LanceDB table — the driver is thin enough that mocking it would
    test nothing."""
    import lancedb

    path = tmp_path / "lancedb"
    db = lancedb.connect(str(path))
    db.create_table(
        "docs",
        data=[
            {
                "id": "a",
                "text": "docking clamps",
                "metadata": json.dumps({"kind": "note"}),
                "vector": [1.0, 0.0],
            },
            {
                "id": "b",
                "text": "lunch menu",
                "metadata": json.dumps({"kind": "misc"}),
                "vector": [0.0, 1.0],
            },
        ],
    )
    return {"path": str(path)}


def _run(config, body, **kwargs):
    from backend.modules.database.drivers import lancedb_driver

    return lancedb_driver.run_query(config, json.dumps(body), **kwargs)


def test_lancedb_collections_and_count(lance_store) -> None:
    result = _run(lance_store, {"op": "collections"}, read_only=True)
    assert [c.name for c in result.columns] == ["collection", "rows"]
    assert result.rows == [["docs", 2]]

    counted = _run(lance_store, {"op": "count", "collection": "docs"}, read_only=True)
    assert counted.rows[0][1] == 2


def test_lancedb_metadata_is_hoisted_into_columns(lance_store) -> None:
    """The JSON metadata blob must become real columns, or filtering by eye is
    impossible."""
    result = _run(lance_store, {"op": "list", "collection": "docs"}, read_only=True)
    assert "kind" in [c.name for c in result.columns]


def test_lancedb_search_ranks_by_similarity(lance_store) -> None:
    result = _run(
        lance_store,
        {"op": "search", "collection": "docs", "vector": [1.0, 0.0], "limit": 2},
        read_only=True,
    )
    names = [c.name for c in result.columns]
    assert "score" in names  # cosine default => a similarity is meaningful
    assert result.rows[0][names.index("id")] == "a"


def test_lancedb_l2_metric_reports_distance_without_a_fake_score(lance_store) -> None:
    result = _run(
        lance_store,
        {
            "op": "search",
            "collection": "docs",
            "vector": [1.0, 0.0],
            "metric": "l2",
            "limit": 2,
        },
        read_only=True,
    )
    names = [c.name for c in result.columns]
    assert "distance" in names
    assert "score" not in names


def test_lancedb_filters_on_metadata_keys_that_are_not_physical_columns(
    lance_store,
) -> None:
    """`kind` lives inside the JSON blob; Lance can't push it down, so the driver
    post-filters and says so rather than erroring with 'No field named kind'."""
    result = _run(
        lance_store,
        {"op": "list", "collection": "docs", "where": {"kind": "note"}},
        read_only=True,
    )
    assert result.rowcount == 1
    assert result.message and "client-side" in result.message


def test_lancedb_pushes_down_physical_columns_silently(lance_store) -> None:
    result = _run(
        lance_store,
        {"op": "list", "collection": "docs", "where": {"id": "b"}},
        read_only=True,
    )
    assert result.rowcount == 1
    assert result.message is None  # pushed down; nothing to warn about


def test_lancedb_get_by_ids(lance_store) -> None:
    result = _run(
        lance_store, {"op": "get", "collection": "docs", "ids": ["a"]}, read_only=True
    )
    assert result.rowcount == 1


def test_lancedb_read_only_blocks_writes(lance_store) -> None:
    with pytest.raises(DriverError, match="read-only"):
        _run(
            lance_store,
            {"op": "delete", "collection": "docs", "ids": ["a"]},
            read_only=True,
        )


def test_lancedb_missing_collection_is_a_clean_error(lance_store) -> None:
    with pytest.raises(DriverError, match="nope"):
        _run(lance_store, {"op": "count", "collection": "nope"}, read_only=True)


def test_lancedb_introspect_lists_collections_as_tables(lance_store) -> None:
    from backend.modules.database.drivers import lancedb_driver

    schema = lancedb_driver.introspect(lance_store)
    assert [t.name for t in schema.tables] == ["docs"]
    assert {"id", "text", "vector"} <= {c.name for c in schema.tables[0].columns}


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_builtin_vectors_connection_is_exposed(client: TestClient) -> None:
    """The whole point of the feature: the node's vector store is reachable."""
    data = client.get("/api/database/connections").json()
    by_id = {c["id"]: c for c in data["connections"]}
    assert by_id["app"]["dialect"] == "sql"
    assert by_id["vectors"]["provider"] == "lancedb"
    assert by_id["vectors"]["dialect"] == "json"
    assert by_id["vectors"]["builtin"] is True


def test_builtin_vectors_connection_cannot_be_deleted(client: TestClient) -> None:
    res = client.delete("/api/database/connections/vectors")
    assert res.status_code == 400
    assert "read-only" in res.json()["detail"]


def test_json_read_only_rejection_explains_the_json_ops(client: TestClient) -> None:
    res = client.post(
        "/api/database/query",
        json={
            "connection_id": "vectors",
            "sql": json.dumps({"op": "delete", "collection": "x", "ids": ["a"]}),
            "read_only": True,
        },
    )
    assert res.status_code == 400
    # The SQL wording would be actively misleading on a vector connection.
    assert "SELECT" not in res.json()["detail"]
    assert "search" in res.json()["detail"]
