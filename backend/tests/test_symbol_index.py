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


# --- Import-statement completion ---------------------------------------------
#
# `from <Tab>` and `from vllm import <Tab>` ask questions the identifier prefix
# query cannot answer: the first has no prefix at all, the second wants one module's
# importable names rather than a global scan.


def _seed_package_rows():
    """A miniature symdex projection: two modules of one package, plus a method.

    The method matters — it is stored with `module` = its *class* and an empty `imp`,
    which is the whole reason the member query filters on `imp` rather than `module`."""
    symbol_store.replace_source(
        "pkg:vllm",
        "python",
        [
            {
                "symbol": "LLM",
                "kind": "class",
                "detail": "class vllm.LLM",
                "imp": "vllm",
            },
            {"symbol": "SamplingParams", "kind": "class", "detail": "", "imp": "vllm"},
            {
                "symbol": "LoRARequest",
                "kind": "class",
                "detail": "",
                "imp": "vllm.lora.request",
            },
            # A method: belongs to a class, importable from nothing.
            {"symbol": "generate", "kind": "function", "module": "LLM", "imp": ""},
        ],
    )


def test_query_modules_ranks_shallow_first():
    _seed_package_rows()
    names = [r["module"] for r in symbol_store.query_modules("python", "vllm")]
    # The module you reach at the top of a package is the one people mean.
    assert names.index("vllm") < names.index("vllm.lora.request")


def test_query_modules_with_no_prefix_lists_top_level_only():
    _seed_package_rows()
    names = [r["module"] for r in symbol_store.query_modules("python", "")]
    # `from <Tab>` is a real question; listing every dotted submodule is not the answer.
    assert "vllm" in names
    assert "vllm.lora.request" not in names


def test_query_import_members_accepts_an_empty_prefix():
    _seed_package_rows()
    # The exact regression copying `query`'s `if not prefix: return []` would cause —
    # `from vllm import <Tab>` is the case this whole path exists for.
    names = [
        r["symbol"] for r in symbol_store.query_import_members("python", "vllm", "")
    ]
    assert names == sorted(["LLM", "SamplingParams"], key=lambda s: (len(s), s))


def test_query_import_members_filters_on_imp_not_module():
    _seed_package_rows()
    # `generate` lives on the class LLM (module='LLM', imp=''); a `module = ?` filter
    # would answer `from LLM import <Tab>` with it, and miss the package's own names.
    assert symbol_store.query_import_members("python", "LLM") == []
    names = [r["symbol"] for r in symbol_store.query_import_members("python", "vllm")]
    assert "generate" not in names


def test_import_completion_routes_return_the_new_fields():
    """Through HTTP, not the store — a response model that omits a field drops it
    silently, and the browser sees `undefined` rather than an error."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.modules.lsp.routes import router

    _seed_package_rows()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    mods = client.get(
        "/api/editor/complete/modules", params={"lang": "python", "prefix": "vllm"}
    )
    assert mods.status_code == 200
    assert "vllm" in [i["module"] for i in mods.json()["items"]]

    members = client.get(
        "/api/editor/complete/members", params={"lang": "python", "module": "vllm"}
    )
    assert members.status_code == 200
    items = members.json()["items"]
    assert {i["symbol"] for i in items} == {"LLM", "SamplingParams"}
    assert next(i for i in items if i["symbol"] == "LLM")["detail"] == "class vllm.LLM"
