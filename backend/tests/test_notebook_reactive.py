"""Static analysis + dependency-graph unit tests for the reactive engine."""

from backend.notebook_core.reactive import ReactiveGraph, analyze


def test_simple_def_and_ref():
    assert analyze("x = 1").defs == {"x"}
    a = analyze("b = a + 1")
    assert a.defs == {"b"}
    assert a.refs == {"a"}


def test_builtins_and_self_not_deps():
    a = analyze("y = len(x) + 1")  # len is builtin, x is a dep
    assert a.refs == {"x"}
    a2 = analyze("x = x + 1")  # augmented-style self ref removed
    assert "x" not in a2.refs


def test_function_reading_global_is_a_dep():
    a = analyze("def f():\n    return config")
    assert a.defs == {"f"}
    assert a.refs == {"config"}


def test_comprehension_target_is_local():
    a = analyze("out = [i * k for i in range(n)]")
    assert a.defs == {"out"}
    assert a.refs == {"k", "n"}  # i is comprehension-local; range is builtin


def test_import_binds_top_name():
    assert analyze("import a.b.c").defs == {"a"}
    assert analyze("import a.b as c").defs == {"c"}
    assert analyze("from x import *").star_import is True


def test_syntax_error_is_captured():
    a = analyze("def (")
    assert a.parse_error is not None


def test_graph_edges_and_run_order():
    g = ReactiveGraph.build([("a", "x = 1"), ("b", "y = x + 1"), ("c", "print(y)")])
    assert not g.diagnostics
    assert g.edges["a"] == {"b"}
    assert g.edges["b"] == {"c"}
    assert g.run_order("a") == ["a", "b", "c"]
    assert g.run_order("b") == ["b", "c"]
    assert g.downstream("a") == {"b", "c"}


def test_multiple_defs_diagnostic():
    g = ReactiveGraph.build([("a", "x = 1"), ("b", "x = 2")])
    kinds = {(d.cellId, d.kind) for d in g.diagnostics}
    assert ("a", "multiple_defs") in kinds
    assert ("b", "multiple_defs") in kinds
    assert "x" not in g.provider  # ambiguous → no edges


def test_cycle_diagnostic():
    g = ReactiveGraph.build([("a", "x = y"), ("b", "y = x")])
    cycle_cells = {d.cellId for d in g.diagnostics if d.kind == "cycle"}
    assert cycle_cells == {"a", "b"}


def test_stale_provider_diff():
    before = ReactiveGraph.build([("a", "x = 1"), ("b", "y = x")])
    after = ReactiveGraph.build([("b", "y = x")])  # cell a deleted
    stale = set(before.provider) - set(after.provider)
    assert "x" in stale
