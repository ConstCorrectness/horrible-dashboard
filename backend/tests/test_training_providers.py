"""Provider layer: protocol conformance, gymnasium catalog, kaggle/hf via mocked
lazy client factories, and the backend-sdk plugin seam."""

from pathlib import Path

import pytest

from backend.modules.training.models import EnvironmentRefModel, ProjectModel
from backend.modules.training.providers import (
    ProviderError,
    get_provider,
    list_providers,
)
from backend.modules.training.providers.base import (
    EnvironmentProvider,
    FetchResult,
    ScaffoldResult,
)
from backend.sdk.registry import registry as sdk_registry


def _project(tmp_path: Path) -> ProjectModel:
    return ProjectModel(id="p", name="p", root=str(tmp_path))


def test_builtins_registered() -> None:
    infos = {p.provider: p for p in list_providers()}
    assert set(infos) >= {"kaggle", "huggingface", "gymnasium"}
    assert infos["kaggle"].kinds == ["competition", "dataset"]
    assert infos["gymnasium"].kinds == ["env"]
    for provider_id in ("kaggle", "huggingface", "gymnasium"):
        assert isinstance(get_provider(provider_id), EnvironmentProvider)


def test_unknown_provider_raises() -> None:
    with pytest.raises(ProviderError, match="unknown environment provider"):
        get_provider("nope")


# --- gymnasium (pure catalog, no client) -------------------------------------


def test_gym_search_and_resolve() -> None:
    gym = get_provider("gymnasium")
    hits = gym.search("cartpole", None, 10)
    assert [h.id for h in hits] == ["CartPole-v1"]
    ref = gym.resolve("CartPole-v1", None)
    assert ref.meta["namespace"] == "classic-control"
    custom = gym.resolve("MyCustomEnv-v0", None)
    assert custom.meta["curated"] is False


def test_gym_fetch_is_noop_and_scaffold_has_requirements(tmp_path: Path) -> None:
    gym = get_provider("gymnasium")
    ref = gym.resolve("LunarLander-v3", None)
    result = gym.fetch(ref, tmp_path / "data", lambda m, p=None: None)
    assert isinstance(result, FetchResult) and result.files == []
    scaffold = gym.scaffold(ref, _project(tmp_path))
    assert "gymnasium[box2d]" in scaffold.requirements
    assert any("gym.make" in c.get("source", "") for c in scaffold.cells)


# --- kaggle (mocked client factory) ------------------------------------------


class _FakeCompetition:
    ref = "https://www.kaggle.com/competitions/pokemon-tcg"
    title = "Pokemon TCG"
    deadline = "2026-12-31"
    reward = "Swag"
    category = "Playground"


class _FakeKaggleApi:
    def __init__(self) -> None:
        self.downloads: list[tuple[str, str]] = []

    def competitions_list(self, search: str = ""):
        return [_FakeCompetition()] if "pokemon" in search.lower() else []

    def dataset_list(self, search: str = ""):
        return []

    def competition_download_files(self, cid: str, path: str, quiet: bool = True):
        self.downloads.append((cid, path))
        import zipfile

        with zipfile.ZipFile(Path(path) / f"{cid}.zip", "w") as zf:
            zf.writestr("train.csv", "a,b\n1,2\n")


@pytest.fixture
def fake_kaggle(monkeypatch) -> _FakeKaggleApi:
    from backend.modules.training.providers import kaggle_provider

    api = _FakeKaggleApi()
    monkeypatch.setattr(kaggle_provider, "_api", lambda: api)
    return api


def test_kaggle_search_resolve_fetch(tmp_path: Path, fake_kaggle) -> None:
    kaggle = get_provider("kaggle")
    hits = kaggle.search("pokemon tcg", "competition", 5)
    assert hits and hits[0].id == "pokemon-tcg" and hits[0].kind == "competition"

    ref = kaggle.resolve("pokemon-tcg", "competition")
    assert ref.title == "Pokemon TCG"

    dest = tmp_path / "data"
    messages: list[str] = []
    result = kaggle.fetch(ref, dest, lambda m, p=None: messages.append(m))
    assert result.files == ["train.csv"]  # zip unpacked and removed
    assert not list(dest.glob("*.zip"))
    assert (dest / "train.csv").read_text() == "a,b\n1,2\n"
    assert fake_kaggle.downloads == [("pokemon-tcg", str(dest))]

    scaffold = kaggle.scaffold(ref, _project(tmp_path))
    assert "pandas" in scaffold.requirements
    assert any("read_csv" in c.get("source", "") for c in scaffold.cells)


def test_kaggle_resolve_unknown_raises(fake_kaggle) -> None:
    kaggle = get_provider("kaggle")
    with pytest.raises(ProviderError, match="not found"):
        kaggle.resolve("does-not-exist", "competition")


# --- huggingface (mocked client factory) -------------------------------------


class _FakeDatasetInfo:
    def __init__(self, did: str) -> None:
        self.id = did
        self.downloads = 7
        self.likes = 3


class _FakeHfApi:
    def list_datasets(self, search: str = "", limit: int = 20):
        return [_FakeDatasetInfo("org/pokemon-cards")] if "pokemon" in search else []

    def dataset_info(self, did: str):
        if did != "org/pokemon-cards":
            raise ValueError("not found")
        return _FakeDatasetInfo(did)


@pytest.fixture
def fake_hf(monkeypatch) -> _FakeHfApi:
    from backend.modules.training.providers import huggingface_provider

    api = _FakeHfApi()
    monkeypatch.setattr(huggingface_provider, "_api", lambda: api)
    return api


def test_hf_search_resolve_lazy_fetch(tmp_path: Path, fake_hf) -> None:
    hf = get_provider("huggingface")
    hits = hf.search("pokemon", None, 5)
    assert hits and hits[0].id == "org/pokemon-cards"

    ref = hf.resolve("org/pokemon-cards", None)
    result = hf.fetch(ref, tmp_path / "data", lambda m, p=None: None)
    assert "lazy" in result.note
    assert not (tmp_path / "data").exists()  # nothing downloaded

    scaffold = hf.scaffold(ref, _project(tmp_path))
    assert "datasets" in scaffold.requirements
    assert any("load_dataset" in c.get("source", "") for c in scaffold.cells)


# --- plugin seam --------------------------------------------------------------


class _PluginProvider:
    provider = "fakeenv"
    label = "Fake Env"
    kinds = ("dataset",)

    def search(self, query, kind, limit):
        return [
            EnvironmentRefModel(
                provider="fakeenv", kind="dataset", id="fake-1", title="Fake"
            )
        ]

    def resolve(self, ref_id, kind):
        return EnvironmentRefModel(
            provider="fakeenv", kind="dataset", id=ref_id, title="Fake"
        )

    def fetch(self, ref, dest, progress):
        return FetchResult(note="fake")

    def scaffold(self, ref, project):
        return ScaffoldResult(cells=[], requirements=[])


def test_plugin_provider_via_sdk_seam() -> None:
    from backend.sdk.host import PluginHost
    from backend.sdk.types import PluginManifest

    host = PluginHost(PluginManifest(id="test", name="test"), sdk_registry)
    host.add_training_provider(_PluginProvider())
    try:
        assert get_provider("fakeenv").label == "Fake Env"
        assert any(p.provider == "fakeenv" for p in list_providers())
    finally:
        sdk_registry.training_providers.clear()
