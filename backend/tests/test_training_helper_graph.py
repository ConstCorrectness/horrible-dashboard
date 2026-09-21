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


@pytest.fixture
def fake_transformers(monkeypatch):
    """A stand-in `transformers` for `ht.callback()`.

    The helper imports `TrainerCallback` *inside* the function precisely so the
    package keeps zero dependencies, which means the import is interceptable and
    this test does not need transformers (or torch) installed in the backend env.
    The base class contributes nothing the callback relies on.
    """
    import sys
    import types

    module = types.ModuleType("transformers")
    module.TrainerCallback = type("TrainerCallback", (), {})
    monkeypatch.setitem(sys.modules, "transformers", module)
    return module


def _events(capsys, ht) -> list[dict]:
    out = capsys.readouterr().out
    return [
        json.loads(ln[len(ht.SENTINEL) :])
        for ln in out.splitlines()
        if ln.startswith(ht.SENTINEL)
    ]


def test_callback_publishes_the_architecture_it_is_handed(
    ht, capsys, fake_transformers
) -> None:
    """The bug this closes: a recipe ran to completion and the Architecture pane
    still said "no model yet". The Trainer hands `model=` to every hook and the
    callback dropped it, so nothing ever published a graph for a notebook the
    user had not hand-edited."""
    cb = ht.callback(name="run-under-test")
    cb.on_train_begin(None, None, None, model=_fake_model())
    kinds = [e["type"] for e in _events(capsys, ht)]
    assert kinds == ["run", "model_graph"]


def test_callback_graph_can_be_turned_off(ht, capsys, fake_transformers) -> None:
    cb = ht.callback(graph=False)
    cb.on_train_begin(None, None, None, model=_fake_model())
    assert [e["type"] for e in _events(capsys, ht)] == ["run"]


def test_callback_without_a_model_still_starts_the_run(
    ht, capsys, fake_transformers
) -> None:
    # A Trainer subclass that does not pass `model=` must not take the run with
    # it — metrics are the callback's primary job and the graph is a bonus.
    cb = ht.callback()
    cb.on_train_begin(None, None, None)
    assert [e["type"] for e in _events(capsys, ht)] == ["run"]


class _NormedParam(_FakeParam):
    """A parameter `_emit_stats` can actually measure: it asks for
    `p.detach().norm()`, which the plain fake has no answer for."""

    def detach(self):
        return self

    def norm(self) -> float:
        return float(self._n)


def _normed_model() -> _FakeModule:
    model = _FakeModule({"head": _FakeModule()})
    model._children["head"]._params = [_NormedParam(9)]
    return model


def _step(n: int):
    return type("S", (), {"global_step": n})()


def test_callback_weights_are_opt_in(ht, capsys, fake_transformers) -> None:
    """`weights=True` is what costs: it norms every parameter on every `log()`.
    Off, a logged metric is one event; on, the stats ride with it."""
    cb = ht.callback()
    cb.on_train_begin(None, None, None, model=_normed_model())
    capsys.readouterr()
    cb.on_log(None, _step(1), None, logs={"loss": 0.5})
    assert [e["type"] for e in _events(capsys, ht)] == ["metric"]

    cb2 = ht.callback(weights=True)
    cb2.on_train_begin(None, None, None, model=_normed_model())
    capsys.readouterr()
    cb2.on_log(None, _step(1), None, logs={"loss": 0.5})
    assert [e["type"] for e in _events(capsys, ht)] == ["metric", "model_stats"]


def test_a_broken_model_does_not_break_the_run(ht, capsys, fake_transformers) -> None:
    class Exploding:
        def named_modules(self, prefix: str = ""):
            raise RuntimeError("not a real model")

    cb = ht.callback()
    cb.on_train_begin(None, None, None, model=Exploding())
    # The run started; the graph is simply absent. A training loop must never die
    # of a visualisation.
    assert [e["type"] for e in _events(capsys, ht)] == ["run"]
