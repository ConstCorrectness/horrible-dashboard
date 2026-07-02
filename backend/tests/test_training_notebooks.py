"""nbformat IO: round-trip, id normalization, atomic save, and cell ops."""

from pathlib import Path

import nbformat
import pytest

from backend.modules.training import notebooks
from backend.modules.training.models import ProjectModel
from backend.modules.training.providers.base import code_cell, md_cell


@pytest.fixture
def project(tmp_path) -> ProjectModel:
    root = tmp_path / "proj"
    root.mkdir()
    return ProjectModel(id="proj", name="Proj", root=str(root))


def test_new_notebook_round_trip(project: ProjectModel) -> None:
    notebooks.new_notebook(
        project, "main.ipynb", [md_cell("# Hi"), code_cell("print('x')")]
    )
    path = notebooks.notebook_path(project, "main.ipynb")
    nb = notebooks.load(path)
    assert [c.cell_type for c in nb.cells] == ["markdown", "code"]
    assert all(c.get("id") for c in nb.cells)
    assert nb.metadata["horrible"]["projectId"] == "proj"

    model = notebooks.to_model(nb, "main.ipynb")
    rebuilt = notebooks.from_model(model)
    assert [c["source"] for c in rebuilt.cells] == [c["source"] for c in nb.cells]
    assert [c["id"] for c in rebuilt.cells] == [c["id"] for c in nb.cells]


def test_notebook_path_rejects_escape(project: ProjectModel) -> None:
    with pytest.raises(ValueError, match="escapes project root"):
        notebooks.notebook_path(project, "../outside.ipynb")


def test_atomic_save_leaves_no_temp(project: ProjectModel) -> None:
    notebooks.new_notebook(project, "main.ipynb", [code_cell("1")])
    path = notebooks.notebook_path(project, "main.ipynb")
    nb = notebooks.load(path)
    notebooks.save(path, nb)
    leftovers = list(Path(project.root).glob("*.ipynb.tmp"))
    assert leftovers == []
    assert nbformat.read(str(path), as_version=4).cells[0].source == "1"


def test_legacy_notebook_gets_cell_ids(project: ProjectModel) -> None:
    # nbformat 4.4 predates cell ids; load() must normalize them in.
    nb = nbformat.v4.new_notebook()
    cell = nbformat.v4.new_code_cell("x = 1")
    del cell["id"]
    nb.cells.append(cell)
    nb.nbformat_minor = 4
    path = notebooks.notebook_path(project, "old.ipynb")
    with open(path, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)
    loaded = notebooks.load(path)
    assert loaded.cells[0].get("id")


class TestApplyOp:
    @pytest.fixture
    def nb(self, project: ProjectModel):
        notebooks.new_notebook(project, "main.ipynb", [code_cell("a"), code_cell("b")])
        return notebooks.load(notebooks.notebook_path(project, "main.ipynb"))

    def test_insert_at_end_and_after(self, nb) -> None:
        new_id = notebooks.apply_op(nb, {"op": "insert", "source": "c"})
        assert nb.cells[-1]["source"] == "c" and nb.cells[-1]["id"] == new_id
        first = nb.cells[0]["id"]
        notebooks.apply_op(
            nb,
            {
                "op": "insert",
                "afterCellId": first,
                "source": "mid",
                "cellType": "markdown",
            },
        )
        assert nb.cells[1]["source"] == "mid"
        assert nb.cells[1]["cell_type"] == "markdown"

    def test_edit_delete_move(self, nb) -> None:
        first, second = nb.cells[0]["id"], nb.cells[1]["id"]
        notebooks.apply_op(nb, {"op": "edit", "cellId": first, "source": "a2"})
        assert nb.cells[0]["source"] == "a2"
        notebooks.apply_op(nb, {"op": "move", "cellId": first, "index": 1})
        assert [c["id"] for c in nb.cells] == [second, first]
        notebooks.apply_op(nb, {"op": "delete", "cellId": second})
        assert [c["id"] for c in nb.cells] == [first]

    def test_unknown_op_and_cell_raise(self, nb) -> None:
        with pytest.raises(ValueError, match="unknown cell op"):
            notebooks.apply_op(nb, {"op": "explode", "cellId": nb.cells[0]["id"]})
        with pytest.raises(ValueError, match="unknown cell"):
            notebooks.apply_op(nb, {"op": "edit", "cellId": "nope", "source": ""})
