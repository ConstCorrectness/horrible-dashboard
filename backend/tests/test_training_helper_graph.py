"""horrible_train.watch() graph extraction.

The backend env deliberately has no torch, so the fx path is exercised only when
torch happens to be importable (skip otherwise); the `named_modules` fallback is
tested with a duck-typed fake model — which is also the path any untraceable
model takes.
"""

import importlib.util
import json
import pathlib

import pytest

HELPER = pathlib.Path("backend/modules/training/helper/horrible_train/__init__.py")


@pytest.fixture
def ht():
    spec = importlib.util.spec_from_file_location("horrible_train_graph_test", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeParam:
    def __init__(self, n: int) -> None:
        self._n = n
        self.grad = None

    def numel(self) -> int:
        return self._n


class _FakeModule:
    """Duck-types the bits of nn.Module the fallback path touches."""

    def __init__(self, children: dict | None = None, params: int = 0) -> None:
        self._children = children or {}
        self._params = [_FakeParam(params)] if params else []

    def named_modules(self, prefix: str = ""):
        yield prefix, self
        for name, child in self._children.items():
            sub = f"{prefix}.{name}" if prefix else name
            yield from child.named_modules(sub)

    def parameters(self, recurse: bool = True):
        yield from self._params
        if recurse:
            for child in self._children.values():
                yield from child.parameters()


def _fake_model() -> _FakeModule:
    return _FakeModule(
        {
            "encoder": _FakeModule({"fc1": _FakeModule(params=128)}),
            "head": _FakeModule(params=10),
        }
    )


def test_fallback_module_tree(ht) -> None:
    graph = ht._module_tree(_fake_model())
    assert graph["kind"] == "modules"
    ids = {n["id"] for n in graph["nodes"]}
    assert ids == {"model", "encoder", "encoder.fc1", "head"}
    fc1 = next(n for n in graph["nodes"] if n["id"] == "encoder.fc1")
    assert fc1["params"] == 128
    edges = {(e["from"], e["to"]) for e in graph["edges"]}
    assert ("model", "encoder") in edges
    assert ("encoder", "encoder.fc1") in edges


def test_watch_emits_graph_event(ht, capsys) -> None:
    ht.watch(_fake_model())
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith(ht.SENTINEL))
    event = json.loads(line[len(ht.SENTINEL) :])
    assert event["type"] == "model_graph"
    # fx trace fails on the fake model → fallback kicks in, never raises.
    assert event["graph"]["kind"] == "modules"


def test_fx_graph_with_real_torch(ht, capsys) -> None:
    torch = pytest.importorskip("torch")
    model = torch.nn.Sequential(
        torch.nn.Linear(4, 8), torch.nn.ReLU(), torch.nn.Linear(8, 2)
    )
    ht.watch(model, example=torch.zeros(1, 4))
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith(ht.SENTINEL))
    event = json.loads(line[len(ht.SENTINEL) :])
    graph = event["graph"]
    assert graph["kind"] == "fx"
    ops = [n["op"] for n in graph["nodes"]]
    assert "Linear" in ops and "ReLU" in ops
    linear = next(n for n in graph["nodes"] if n["op"] == "Linear")
    assert linear["params"] == 4 * 8 + 8
    assert any(n["shape"] == [1, 2] for n in graph["nodes"])
