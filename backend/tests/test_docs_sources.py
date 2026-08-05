"""The documentation chain's server half.

The parts worth pinning are the ones that decide whether a popup shows the right
thing or quietly the wrong thing: ANSI stripping (IPython formats `inspect_reply`
for a terminal), signature extraction, source ordering, and the rule that one
broken source must not sink the rest.
"""

from __future__ import annotations

import asyncio

from backend.modules.docs import sources as S
from backend.modules.docs.models import DocEntry, DocLookupContext


def test_strip_ansi_removes_ipython_terminal_colour() -> None:
    # IPython renders inspect output for a terminal, so the reply arrives full of
    # escapes that would otherwise show as literal `[0;31m` in the popup.
    raw = "\x1b[0;31mSignature:\x1b[0m \x1b[0;32mdumps(obj)\x1b[0m"
    assert S.strip_ansi(raw) == "Signature: dumps(obj)"


def test_split_signature_lifts_the_header_line() -> None:
    body = "Signature: dumps(obj, **kw)\nDocstring:\nSerialize obj to JSON."
    sig, rest = S._split_signature(body)
    assert sig == "dumps(obj, **kw)"
    assert "Signature:" not in rest
    assert rest.startswith("Docstring:")


def test_split_signature_handles_init_signature() -> None:
    sig, _ = S._split_signature("Init signature: Path(*args)\nDocstring: paths")
    assert sig == "Path(*args)"


def test_split_signature_leaves_a_body_without_one_alone() -> None:
    sig, rest = S._split_signature("Docstring:\njust prose")
    assert sig == ""
    assert rest == "Docstring:\njust prose"


def test_truncate_marks_the_cut() -> None:
    # A full pandas docstring is ~40 KB; a silent cut reads as the end of the text.
    out = S._truncate("x" * (S.MAX_BODY_CHARS + 500))
    assert out.endswith("… (truncated)")
    assert len(out) < S.MAX_BODY_CHARS + 100


def test_truncate_leaves_a_short_body_untouched() -> None:
    assert S._truncate("short") == "short"


def test_chain_stops_at_the_first_source_that_answers(monkeypatch) -> None:
    calls: list[str] = []

    async def kernel(symbol, context):
        calls.append("kernel")
        return []

    async def index(symbol, lang):
        calls.append("index")
        return [DocEntry(source="index", title=symbol, body="from the index")]

    async def web(symbol, lang):
        calls.append("web")
        return [DocEntry(source="web", title=symbol, body="from the web")]

    monkeypatch.setattr(S, "lookup_kernel", kernel)
    monkeypatch.setattr(S, "lookup_index", index)
    monkeypatch.setattr(S, "lookup_web", web)

    entries, tried, notes = asyncio.run(
        S.resolve_docs("json.dumps", ["kernel", "index", "web"], None, "python")
    )
    assert [e.source for e in entries] == ["index"]
    # `web` is never asked — it is the expensive one, and that is the point of the order.
    assert calls == ["kernel", "index"]
    assert tried == ["kernel", "index"]
    assert notes == []


def test_chain_survives_a_source_that_raises(monkeypatch) -> None:
    async def boom(symbol, lang):
        raise RuntimeError("index is broken")

    async def index_ok(symbol, lang):
        return [DocEntry(source="web", title=symbol, body="ok")]

    monkeypatch.setattr(S, "lookup_index", boom)
    monkeypatch.setattr(S, "lookup_web", index_ok)

    entries, tried, notes = asyncio.run(
        S.resolve_docs("x", ["index", "web"], None, "python")
    )
    assert [e.source for e in entries] == ["web"]
    assert tried == ["index", "web"]
    assert any("index failed" in n for n in notes)


def test_chain_reports_what_it_tried_when_nothing_answers(monkeypatch) -> None:
    async def empty_index(symbol, lang):
        return []

    monkeypatch.setattr(S, "lookup_index", empty_index)
    entries, tried, _ = asyncio.run(S.resolve_docs("x", ["index"], None, "python"))
    # "nothing has docs for this" and "no source was enabled" are different answers;
    # `tried` is what lets the caller tell them apart.
    assert entries == []
    assert tried == ["index"]


def test_no_enabled_sources_tries_nothing() -> None:
    entries, tried, _ = asyncio.run(S.resolve_docs("x", [], None, "python"))
    assert entries == []
    assert tried == []


def test_kernel_source_is_skipped_without_a_notebook() -> None:
    # The editor has no notebook, so this source costs one dict lookup and no RPC.
    assert asyncio.run(S.lookup_kernel("x", None)) == []
    assert asyncio.run(S.lookup_kernel("x", DocLookupContext())) == []


def test_index_source_prefers_the_dotted_module(monkeypatch) -> None:
    rows = [
        {
            "symbol": "merge",
            "module": "other.thing",
            "detail": "merge(a)",
            "doc": "wrong one",
        },
        {
            "symbol": "merge",
            "module": "pandas.core.frame",
            "detail": "merge(right)",
            "doc": "right one",
        },
    ]
    import backend.modules.lsp.symbol_store as store

    monkeypatch.setattr(store, "query", lambda lang, name, limit, member_of: rows)
    entries = asyncio.run(S.lookup_index("pandas.DataFrame.merge", "python"))
    assert entries and entries[0].body == "right one"


def test_index_source_returns_nothing_when_the_row_has_no_docs(
    monkeypatch,
) -> None:
    import backend.modules.lsp.symbol_store as store

    monkeypatch.setattr(
        store,
        "query",
        lambda lang, name, limit, member_of: [
            {"symbol": "x", "module": "m", "detail": "", "doc": ""}
        ],
    )
    # A name with neither a signature nor a docstring is a hit, not an answer —
    # showing an empty popup is worse than falling through to the next source.
    assert asyncio.run(S.lookup_index("x", "python")) == []
