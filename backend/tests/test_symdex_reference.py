"""The Python reference pane's backend: browsing `code_symbols`, whole docstrings
read by AST, and upstream links pinned to the installed version."""

from __future__ import annotations

import textwrap

import pytest

from backend.modules.lsp import symbol_store
from backend.modules.symdex import extract_packages as ep
from backend.modules.symdex import reference
from backend.modules.symdex.extract_sdk import extract_sdk


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(symbol_store, "_initialized", False)
    yield


def _site(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    pkg = site / "torch"
    (pkg / "nn").mkdir(parents=True)
    (pkg / "__init__.py").write_text("__all__ = ['matmul', 'Tensor']\n")
    (pkg / "nn" / "__init__.py").write_text("")
    (pkg / "nn" / "linear.py").write_text(
        textwrap.dedent(
            '''
            class Linear:
                """Applies an affine linear transformation.

                Shape:
                    - Input: (*, H_in)
                """

                def forward(self, x):
                    """Run it.

                    Second paragraph kept in full docs."""
                    return x
            '''
        )
    )
    (pkg / "_torch_docs.py").write_text(
        'add_docstr(torch.matmul, r"""\nmatmul(input, other) -> Tensor\n\n'
        'Matrix product.\n\nBroadcasting rules follow.\n""")\n'
    )
    (pkg / "_tensor_docs.py").write_text(
        'add_docstr_all("view", r"""\nview(*shape) -> Tensor\n\nSame data.\n""")\n'
    )
    monkeypatch.setattr(reference, "_roots", lambda interp: (None, (site,)))
    return site


def _index(site):
    docs = ep.harvest_package_dir(site / "torch", "torch", "torch")
    symbol_store.replace_source("pkg:torch", "python", [d.store_row() for d in docs])


def test_members_attach_methods_to_their_class_and_search_scopes_by_module(
    tmp_path, monkeypatch
):
    site = _site(tmp_path, monkeypatch)
    _index(site)
    modules = [m["module"] for m in symbol_store.reference_modules("pkg:torch")]
    assert "torch.nn.linear" in modules
    members = symbol_store.reference_members("pkg:torch", "torch.nn.linear")
    linear = next(m for m in members if m["name"] == "Linear")
    assert [m["name"] for m in linear["members"]] == ["forward"]
    # `Tensor` is only an `__all__` name here, but `Tensor.view` still hangs off it.
    top = symbol_store.reference_members("pkg:torch", "torch")
    tensor = next(m for m in top if m["name"] == "Tensor")
    assert [m["name"] for m in tensor["members"]] == ["view"]
    hits = symbol_store.reference_search("torch.nn.Lin")
    assert [(h["name"], h["module"]) for h in hits] == [("Linear", "torch.nn.linear")]


def test_full_doc_reads_past_the_first_paragraph(tmp_path, monkeypatch):
    _site(tmp_path, monkeypatch)
    doc = reference.full_doc("pkg:torch", "torch.nn.linear", "Linear", "python")
    assert "Shape:" in doc["doc"]  # the index keeps only the first paragraph
    assert doc["line"] > 0 and doc["file"].endswith("linear.py")
    method = reference.full_doc(
        "pkg:torch", "torch.nn.linear", "Linear.forward", "python"
    )
    assert "Second paragraph" in method["doc"] and method["signature"] == "(self, x)"


def test_full_doc_falls_back_to_add_docstr_for_c_functions(tmp_path, monkeypatch):
    _site(tmp_path, monkeypatch)
    doc = reference.full_doc("pkg:torch", "torch", "matmul", "python")
    assert doc["signature"] == "(input, other) -> Tensor"
    assert "Broadcasting rules follow." in doc["doc"]
    view = reference.full_doc("pkg:torch", "torch", "Tensor.view", "python")
    assert view["signature"] == "(*shape) -> Tensor" and "Same data." in view["doc"]


def test_upstream_links_pin_the_installed_version():
    torch = reference.upstream(
        "pkg:torch",
        "torch",
        "matmul",
        versions={"torch": "2.11.0+cu128"},
        python="3.13",
    )
    assert torch and "/docs/2.11/" in torch["url"] and torch["exact"] == "search"
    std = reference.upstream("std:json", "json", "dumps", versions={}, python="3.13")
    assert std and std["url"].endswith("/3.13/library/json.html#json.dumps")
    assert (
        reference.upstream("sdk:dash", "dash.lens", "grid", versions={}, python="3")
        is None
    )


def test_sdk_corpus_indexes_dash_handles_without_an_import_path():
    """`dash` is a REPL global. Completion must never offer `from dash.lens import
    grid`, so its rows carry no import module — unlike `backend.sdk`, which is real."""
    harvests = {h.dist: h for h in extract_sdk()}
    lens = [d for d in harvests["dash"].docs if d.module == "dash.lens"]
    assert {"grid", "attention", "program"} <= {d.symbol for d in lens}
    assert all(d.imp == "" for d in harvests["dash"].docs)
    assert any(d.imp.startswith("backend.sdk") for d in harvests["backend.sdk"].docs)


def test_a_reexported_name_is_followed_to_its_definition(tmp_path, monkeypatch):
    """`torch.nn.utils` re-exports `clip_grad_norm_` from `.clip_grad`; the index
    files it under the package with no docstring. The page must follow the import,
    not report "no docstring" for the most common hit there is."""
    site = _site(tmp_path, monkeypatch)
    utils = site / "torch" / "nn" / "utils"
    utils.mkdir()
    (utils / "__init__.py").write_text("from .clip_grad import clip_grad_norm_\n")
    (utils / "clip_grad.py").write_text(
        'def clip_grad_norm_(parameters, max_norm):\n    """Clip the gradient norm."""\n'
    )
    doc = reference.full_doc("pkg:torch", "torch.nn.utils", "clip_grad_norm_", "python")
    assert doc["definedIn"] == "torch.nn.utils.clip_grad"
    assert doc["doc"] == "Clip the gradient norm."
    assert doc["signature"] == "(parameters, max_norm)"


def test_python_reference_docs_are_current():
    """`docs/reference/python-*.mdx` are generated from the same harvest the pane
    reads. A docstring changed without regenerating fails here, not in a reader's
    hands. Fix: `uv run python -m backend.modules.symdex.gen_reference_docs`."""
    from backend.modules.symdex import gen_reference_docs as gen

    for path, text in gen.render().items():
        assert path.is_file(), f"missing {path.name} — run the generator"
        assert path.read_text(encoding="utf-8") == text, f"{path.name} is stale"
