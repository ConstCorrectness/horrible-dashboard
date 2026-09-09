"""Repo-wide text search.

Two things here carry real weight. The **containment** test is the security one:
a search must never return a file outside the workspace roots, and the check is
ours rather than a subprocess flag's. The **engine parity** test is the
correctness one: two implementations answering the same question must agree, or
installing ripgrep silently changes what the app finds.
"""

import shutil

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.files import providers
from backend.modules.files import search as search_mod

HAS_RG = shutil.which("rg") is not None


@pytest.fixture
def root(tmp_path):
    """A workspace root with a needle in a few places — plus a sibling of the root
    holding the same needle, which must never be found."""
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "app.ts").write_text(
        "const needle = 1;\nconst other = 2;\nexport { needle };\n", encoding="utf-8"
    )
    (ws / "src" / "app.py").write_text("NEEDLE = 3  # needle\n", encoding="utf-8")
    (ws / "notes.md").write_text("a needle in prose\n", encoding="utf-8")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "dep.js").write_text("needle\n", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("needle\n", encoding="utf-8")
    return ws


@pytest.fixture
def client(root, tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_WORKSPACE_ROOTS", str(root))
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    return TestClient(app)


@pytest.fixture(params=["python", "ripgrep"])
def engine(request, monkeypatch):
    """Run each test on both engines. The ripgrep leg is skipped where the binary
    is absent; the Python leg forces the fallback by hiding it."""
    if request.param == "python":
        monkeypatch.setattr(search_mod, "_rg_path", lambda: None)
    elif not HAS_RG:
        pytest.skip("ripgrep not installed")
    return request.param


def _search(client: TestClient, **body):
    res = client.post("/api/files/search", json={"query": "needle", **body})
    assert res.status_code == 200, res.text
    return res.json()


def _paths(result) -> set[str]:
    return {f["path"] for f in result["files"]}


def _flat(result):
    return [m for f in result["files"] for m in f["matches"]]


# --- finding ---------------------------------------------------------------


def test_finds_literal_matches_with_1_based_positions(
    client: TestClient, root, engine
) -> None:
    result = _search(client, include=["app.ts"])
    matches = _flat(result)
    # `needle` appears on line 1 (col 7) and line 3 (col 10).
    assert [(m["line"], m["column"]) for m in matches] == [(1, 7), (3, 10)]
    assert result["engine"] == engine
    # The highlight offsets index the returned text, not the original line.
    first = matches[0]
    assert first["text"][first["match_start"] : first["match_end"]] == "needle"


def test_case_insensitive_by_default_and_sensitive_on_request(
    client: TestClient, root, engine
) -> None:
    insensitive = _search(client, include=["app.py"], query="NEEDLE")
    assert len(_flat(insensitive)) == 2
    sensitive = _search(client, include=["app.py"], query="NEEDLE", case_sensitive=True)
    assert len(_flat(sensitive)) == 1


def test_regex_and_whole_word(client: TestClient, root, engine) -> None:
    assert (
        len(_flat(_search(client, query="need.e", regex=True, include=["app.ts"]))) == 2
    )
    # `NEEDLE` and the comment's `needle` are both whole words; `needles` would not be.
    assert (
        len(_flat(_search(client, query="needle", whole_word=True, include=["app.py"])))
        == 2
    )


def test_include_and_exclude_globs(client: TestClient, root, engine) -> None:
    assert _paths(_search(client, include=["*.md"])) == {str(root / "notes.md")}
    assert str(root / "notes.md") not in _paths(_search(client, exclude=["*.md"]))


def test_default_excludes_prune_node_modules(client: TestClient, root, engine) -> None:
    assert str(root / "node_modules" / "dep.js") not in _paths(_search(client))


# --- the two that matter ---------------------------------------------------


def test_never_returns_a_file_outside_the_roots(
    client: TestClient, root, tmp_path, engine
) -> None:
    """The sibling directory holds the same needle. Nothing may reach it — the
    boundary is ours to enforce, not a subprocess flag's."""
    for path in _paths(_search(client)):
        assert str(tmp_path / "outside") not in path


@pytest.mark.skipif(not HAS_RG, reason="ripgrep not installed")
def test_both_engines_agree(client: TestClient, root, monkeypatch) -> None:
    """Installing ripgrep must not change what the app finds."""
    with_rg = _search(client)
    monkeypatch.setattr(search_mod, "_rg_path", lambda: None)
    without = _search(client)
    assert with_rg["engine"] == "ripgrep" and without["engine"] == "python"

    def key(result):
        return sorted(
            (m["path"], m["line"], m["column"], m["text"]) for m in _flat(result)
        )

    assert key(with_rg) == key(without)


# --- arguments and limits --------------------------------------------------


def test_a_query_beginning_with_a_dash_is_a_query_not_a_flag(
    client: TestClient, root, engine
) -> None:
    (root / "flags.txt").write_text("value --needle here\n", encoding="utf-8")
    result = _search(client, query="--needle")
    assert str(root / "flags.txt") in _paths(result)


def test_max_results_reports_truncated_rather_than_an_empty_tail(
    client: TestClient, root, engine
) -> None:
    result = _search(client, max_results=1)
    assert result["total"] == 1
    assert result["truncated"] is True


def test_invalid_regex_is_an_error_not_no_matches(
    client: TestClient, root, engine
) -> None:
    result = _search(client, query="(unclosed", regex=True)
    assert result["files"] == []
    assert result["error"] is not None


def test_a_virtual_root_says_so_rather_than_403(client: TestClient, root) -> None:
    """A provider path is refused with a message, not handed to `_resolve` — which
    anchors relative paths and would turn `gdrive:/x` into `<root>/gdrive:/x` and a
    misdescribed 403.

    The provider is registered here rather than assumed: `is_virtual` answers for
    *registered* schemes only, so leaning on whatever another test happened to
    leave behind makes this pass or fail on suite ordering.
    """

    class _Stub:
        scheme = "gdrive"
        read_only = True

        async def roots(self):
            return []

        async def list(self, path, *, fresh=False):  # pragma: no cover - not reached
            raise AssertionError("search must not reach the provider")

        async def read(self, path):  # pragma: no cover - not reached
            raise AssertionError("search must not reach the provider")

    providers.register(_Stub())
    try:
        res = client.post(
            "/api/files/search", json={"query": "needle", "root": "gdrive:/folder"}
        )
    finally:
        providers.unregister("gdrive")
    assert res.status_code == 200
    assert res.json()["error"] is not None


def test_a_file_over_the_size_cap_is_skipped(client: TestClient, root, engine) -> None:
    (root / "big.txt").write_text("needle\n" + ("x" * 5000), encoding="utf-8")
    assert str(root / "big.txt") not in _paths(_search(client, max_file_bytes=100))
