"""The DB-backed completion index: AST symbol harvesting + the prefix query that
replaces the old model-backed 'intellisense'. Isolated per test via HORRIBLE_DATA_DIR."""

import pytest

from backend.modules.lsp import symbol_index, symbol_store


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """A fresh app.db per test — reset the process-global init flag so the store
    re-creates + re-seeds against the new HORRIBLE_DATA_DIR."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(symbol_store, "_initialized", False)
    return tmp_path


SAMPLE = """
import os
from collections import OrderedDict as OD

TIMEOUT = 30

class RequestHandler:
    def handle_request(self, payload, *args, **opts):
        parsed_value = payload
        return parsed_value


def request_helper(url):
    return url
"""


def test_harvest_python_extracts_symbols():
    rows = symbol_index.harvest_python(SAMPLE)
    by_name = {r["symbol"]: r for r in rows}

    # defs, class, params, assignments, imports — dunders excluded.
    assert by_name["RequestHandler"]["kind"] == "class"
    assert by_name["handle_request"]["kind"] == "function"
    assert "payload" in by_name["handle_request"]["detail"]
    assert by_name["request_helper"]["kind"] == "function"
    assert by_name["TIMEOUT"]["kind"] == "variable"
    assert by_name["parsed_value"]["kind"] == "variable"
    assert by_name["os"]["kind"] == "module"
    # `import ... as OD` binds the alias, not the original name.
    assert "OD" in by_name and "OrderedDict" not in by_name


def test_harvest_python_tolerates_syntax_error():
    assert symbol_index.harvest_python("def broken(:\n  pass") == []


def test_query_prefix_ranks_and_matches():
    symbol_store.replace_source(
        "workspace-file:/a.py", "python", symbol_index.harvest_python(SAMPLE)
    )

    hits = symbol_store.query("python", "request", limit=10)
    names = [h["symbol"] for h in hits]
    assert "request_helper" in names
    assert "RequestHandler" in names
    # Every hit actually starts with the prefix (case-insensitive).
    assert all(n.lower().startswith("request") for n in names)


def test_query_includes_seeded_builtins():
    # Builtins are seeded on init even with no buffers indexed.
    hits = symbol_store.query("python", "ret", limit=10)  # 'return' keyword
    assert "return" in [h["symbol"] for h in hits]
    prints = symbol_store.query("python", "print", limit=5)
    assert "print" in [h["symbol"] for h in prints]


def test_replace_source_swaps_rows():
    src = "workspace-file:/b.py"
    symbol_store.replace_source(src, "python", [{"symbol": "alpha_one"}])
    assert "alpha_one" in [
        h["symbol"] for h in symbol_store.query("python", "alpha", 5)
    ]

    # Re-indexing the same source drops the old symbols.
    symbol_store.replace_source(src, "python", [{"symbol": "beta_two"}])
    assert "alpha_one" not in [
        h["symbol"] for h in symbol_store.query("python", "alpha", 5)
    ]
    assert "beta_two" in [h["symbol"] for h in symbol_store.query("python", "beta", 5)]


def test_query_empty_prefix_returns_nothing():
    assert symbol_store.query("python", "", 10) == []


def test_query_keeps_same_name_from_different_modules_apart():
    """`Path` exists in pathlib and elsewhere; grouping by symbol alone used to blend
    them into one row — one module's import glued to another's signature and doc."""
    symbol_store.replace_source(
        "std:pathlib",
        "python",
        [
            {
                "symbol": "Path",
                "kind": "class",
                "module": "pathlib",
                "imp": "pathlib",
                "detail": "class",
                "doc": "PurePath subclass that can make system calls.",
            }
        ],
    )
    symbol_store.replace_source(
        "pkg:fastapi",
        "python",
        [
            {
                "symbol": "Path",
                "kind": "function",
                "module": "fastapi.params",
                "imp": "fastapi.params",
                "detail": "(default: Any)",
                "doc": "A path param.",
            }
        ],
    )
    hits = symbol_store.query("python", "Path", 10)
    by_imp = {h["imp"]: h for h in hits}
    assert by_imp["pathlib"]["doc"].startswith("PurePath")
    assert by_imp["fastapi.params"]["detail"] == "(default: Any)"


def test_query_ranks_shallow_imports_and_buries_methods():
    """A bare prefix wants `json.dumps`, not `xmlrpc.client.dumps` and certainly not
    some class's `dumps` method."""
    symbol_store.replace_source(
        "std:json",
        "python",
        [{"symbol": "dumps", "kind": "function", "module": "json", "imp": "json"}],
    )
    symbol_store.replace_source(
        "std:xmlrpc",
        "python",
        [
            {
                "symbol": "dumps",
                "kind": "function",
                "module": "xmlrpc.client",
                "imp": "xmlrpc.client",
            },
            # A method: no import module, and it should not outrank the real answers.
            {"symbol": "dumps", "kind": "method", "module": "Marshaller", "imp": ""},
        ],
    )
    order = [h["imp"] for h in symbol_store.query("python", "dumps", 10)]
    assert order[0] == "json"
    assert order.index("xmlrpc.client") < order.index("")


def test_orphan_buffer_rows_are_purged_on_init(tmp_path, monkeypatch):
    """A deleted file is never re-indexed, so its symbols would linger forever."""
    gone = tmp_path / "deleted.py"
    alive = tmp_path / "alive.py"
    alive.write_text("x = 1\n")
    symbol_store.replace_source(
        f"workspace-file:{gone}", "python", [{"symbol": "ghost_sym"}]
    )
    symbol_store.replace_source(
        f"workspace-file:{alive}", "python", [{"symbol": "live_sym"}]
    )

    monkeypatch.setattr(symbol_store, "_initialized", False)
    symbol_store.init()

    assert symbol_store.query("python", "ghost", 5) == []
    assert [h["symbol"] for h in symbol_store.query("python", "live", 5)] == [
        "live_sym"
    ]
