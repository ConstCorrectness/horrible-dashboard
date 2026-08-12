"""The recipe surface: validation against the installed library, and the emitted code.

The failures worth a test here are the ones that cost a *run*: a kwarg the
installed `trl` renamed (the run dies with a `TypeError` after the dataset has
downloaded), a field emitted hopefully into a library that never accepted it, a
credential that reaches the browser, and a LoRA adapter fed to the base-model
converter.

None of this needs trl, peft or torch installed — `introspect()` is the only part
that touches a venv, and every consumer of it takes an `Introspection` it can be
handed.
"""

from __future__ import annotations

import json
import re

import pytest

from backend.modules.training import convert, recipes, trackers
from backend.modules.training.models import ProjectModel


def intro(sft: list[str] | None = None, lora: list[str] | None = None, **kw):
    accepted = {}
    if sft is not None:
        accepted["sft"] = sft
    if lora is not None:
        accepted["lora"] = lora
    return recipes.Introspection(
        available=bool(accepted),
        accepted=accepted,
        versions=kw.get("versions", {"trl": "0.30.0", "peft": "0.19.0"}),
        extra=kw.get("extra", {}),
    )


def all_names(target: str) -> list[str]:
    return [f.name for f in recipes.fields_for(target)]


# --- resolving a field against the installed library -------------------------


def test_a_renamed_field_is_emitted_under_the_name_the_library_accepts() -> None:
    """`trl` renamed `max_seq_length` to `max_length`.

    A recipe that emits the name this catalog happens to prefer fails with a
    `TypeError` minutes into a run, after the dataset has downloaded — which is
    the whole reason the catalog carries aliases and asks.
    """
    old = intro(
        sft=[n for n in all_names("sft") if n != "max_length"] + ["max_seq_length"]
    )
    resolved = recipes.resolve(recipes.FIELDS[0], old)
    assert recipes.FIELDS[0].name == "max_length"
    assert resolved.emit == "max_seq_length"
    assert resolved.status == "renamed"
    assert "trl 0.30.0" in resolved.note


def test_an_unknown_field_is_dropped_not_emitted_hopefully() -> None:
    stripped = intro(sft=[n for n in all_names("sft") if n != "packing"])
    resolved = next(
        r for r in recipes.resolve_all(stripped) if r.field.name == "packing"
    )
    assert resolved.status == "unsupported"
    assert resolved.emit is None


def test_unvalidated_is_not_the_same_as_unsupported() -> None:
    """The difference decides whether a field is emitted at all.

    `unvalidated` means we never got to ask (no venv, no trl) — emit it and say
    so. `unsupported` means we asked and the answer was no — do not emit it.
    """
    nothing = recipes.Introspection(error="no venv")
    resolved = recipes.resolve(recipes.FIELDS[0], nothing)
    assert resolved.status == "unvalidated"
    assert resolved.emit == recipes.FIELDS[0].name


# --- what gets emitted --------------------------------------------------------


def code_of(cells: list[dict[str, str]]) -> str:
    return "\n".join(c["source"] for c in cells if c["cell_type"] == "code")


def test_dropped_fields_are_absent_from_the_code_and_named_in_the_header() -> None:
    stripped = intro(
        sft=[n for n in all_names("sft") if n != "packing"], lora=all_names("lora")
    )
    cells = recipes.materialize(recipes.Recipe(), stripped)
    header = cells[0]["source"]
    assert "`packing`" in header
    assert "packing=" not in code_of(cells)


def test_renamed_fields_are_emitted_with_the_new_name_and_a_comment() -> None:
    old = intro(
        sft=[n for n in all_names("sft") if n != "max_length"] + ["max_seq_length"],
        lora=all_names("lora"),
    )
    code = code_of(recipes.materialize(recipes.Recipe(), old))
    assert "max_seq_length=1024" in code
    assert "max_length=1024," not in code


def test_the_local_callback_is_always_installed() -> None:
    """Local metrics are authoritative, which has to be true with every tracker
    setting — including none at all."""
    for trackers_choice in (["none"], ["wandb"], ["tensorboard", "mlflow"]):
        recipe = recipes.Recipe(trackers=trackers_choice)
        code = code_of(recipes.materialize(recipe, intro(sft=all_names("sft"))))
        assert "callbacks=[ht.callback()]" in code


def test_report_to_is_a_list_and_none_is_spelled_the_librarys_way() -> None:
    assert recipes.report_to(recipes.Recipe(trackers=["none"])) == ["none"]
    assert recipes.report_to(recipes.Recipe(trackers=[])) == ["none"]
    assert recipes.report_to(recipes.Recipe(trackers=["wandb", "none"])) == ["wandb"]
    # An unknown tracker is dropped rather than passed through to a library that
    # will raise on it.
    assert recipes.report_to(recipes.Recipe(trackers=["nope"])) == ["none"]


def test_lora_cells_are_omitted_entirely_when_lora_is_off() -> None:
    code = code_of(
        recipes.materialize(
            recipes.Recipe(use_lora=False),
            intro(sft=all_names("sft"), lora=all_names("lora")),
        )
    )
    assert "LoraConfig" not in code
    assert "peft_config" not in code


def test_an_unvalidated_recipe_says_so_in_the_notebook() -> None:
    cells = recipes.materialize(
        recipes.Recipe(), recipes.Introspection(error="no venv")
    )
    assert "Not validated against an installed library" in cells[0]["source"]


def test_the_recipe_round_trips_through_its_dict() -> None:
    recipe = recipes.Recipe(
        base_model="meta-llama/Llama-3.2-1B",
        dataset="trl-lib/Capybara",
        trackers=["wandb"],
        values={**recipes.defaults(), "learning_rate": 5e-5},
    )
    again = recipes.Recipe.from_dict(json.loads(json.dumps(recipe.to_dict())))
    assert again.base_model == recipe.base_model
    assert again.values["learning_rate"] == 5e-5
    # A partial dict still yields every field: a recipe saved by an older build
    # must not emit a config missing half its kwargs.
    partial = recipes.Recipe.from_dict({"baseModel": "x"})
    assert set(partial.values) == set(recipes.defaults())


# --- warnings -----------------------------------------------------------------


def test_bf16_and_fp16_together_is_called_out() -> None:
    values = {**recipes.defaults(), "bf16": True, "fp16": True}
    assert any("bf16 and fp16" in w for w in recipes.warnings_for(values, ["none"]))


def test_wandb_without_a_key_is_called_out_before_the_run(monkeypatch) -> None:
    monkeypatch.setattr(trackers, "has_wandb_key", lambda: False)
    monkeypatch.setattr(trackers, "mlflow_uri", lambda: "")
    warnings = recipes.warnings_for(recipes.defaults(), ["wandb"])
    assert any("no API key is connected" in w for w in warnings)


# --- tracker credentials ------------------------------------------------------


def test_tracker_env_is_scoped_to_the_selected_trackers(monkeypatch) -> None:
    """Connecting the tile must not silently start shipping every run somewhere."""
    monkeypatch.setattr(trackers, "wandb_key", lambda: "k-123")
    monkeypatch.setattr(trackers, "mlflow_uri", lambda: "https://mlflow.example")

    assert trackers.env_for(["none"]) == {}
    assert trackers.env_for(["tensorboard"]) == {}
    assert trackers.env_for(["wandb"]) == {"WANDB_API_KEY": "k-123"}
    assert trackers.env_for(["wandb", "mlflow"]) == {
        "WANDB_API_KEY": "k-123",
        "MLFLOW_TRACKING_URI": "https://mlflow.example",
    }


def test_a_tracker_credential_is_never_declared_as_a_setting() -> None:
    """`GET /api/settings` hands the whole bag to the browser, so a key declared
    as a setting is a key that has left the machine.

    Checked against the manifest that actually declares them, because the mistake
    this guards against is someone adding `training.wandb.apiKey` next to the
    Kaggle credentials already there — which would look consistent and be wrong.
    """
    from pathlib import Path

    manifest = Path("packages/core/src/modules/training/index.ts")
    text = manifest.read_text(encoding="utf-8")
    keys = re.findall(r"key:\s*'([^']+)'", text)
    offenders = [
        k
        for k in keys
        if any(word in k.lower() for word in ("wandb", "mlflow", "trackio"))
    ]
    assert offenders == []


def test_the_form_never_prefills_a_stored_secret(monkeypatch) -> None:
    monkeypatch.setattr(trackers, "_get", lambda name: "already-stored")
    fields = trackers._form_step()["fields"]
    assert [f["value"] for f in fields] == ["", ""]
    assert all(f["secret"] for f in fields)
    # Blank means "keep", so the help text has to say that or a reconfigure looks
    # like it wiped the key.
    assert all("Leave blank to keep it." in f["help"] for f in fields)


# --- checkpoint → GGUF --------------------------------------------------------


@pytest.fixture()
def project(tmp_path) -> ProjectModel:
    return ProjectModel(id="proj", name="proj", root=str(tmp_path))


def test_a_lora_adapter_is_not_mistaken_for_a_model(tmp_path, project) -> None:
    """Feeding an adapter to the base converter fails with an error about missing
    weights that reads like a corrupt checkpoint."""
    full = tmp_path / "outputs" / "full"
    full.mkdir(parents=True)
    (full / "config.json").write_text("{}", encoding="utf-8")

    adapter = tmp_path / "outputs" / "adapter"
    adapter.mkdir(parents=True)
    (adapter / "adapter_config.json").write_text(
        json.dumps({"base_model_name_or_path": "meta-llama/Llama-3.2-1B"}),
        encoding="utf-8",
    )

    assert convert.checkpoint_kind(full) == "model"
    assert convert.checkpoint_kind(adapter) == "lora"
    kinds = {c["relPath"]: c["kind"] for c in convert.list_checkpoints(project)}
    assert kinds == {"outputs/full": "model", "outputs/adapter": "lora"}


def test_the_venv_and_caches_are_not_listed_as_checkpoints(tmp_path, project) -> None:
    """A HF cache under the project is full of `config.json` files that are not
    this project's output."""
    for junk in (".venv/lib/site-packages/somepkg", ".cache/huggingface/models--x"):
        directory = tmp_path / junk
        directory.mkdir(parents=True)
        (directory / "config.json").write_text("{}", encoding="utf-8")
    assert convert.list_checkpoints(project) == []


def test_conversion_refuses_a_checkpoint_outside_the_project(project) -> None:
    import asyncio

    async def collect():
        return [
            event
            async for event in convert.run_conversion(
                project, "../../etc", out_type="f16"
            )
        ]

    events = asyncio.run(collect())
    assert events and "escapes the project root" in events[0]["error"]


def test_conversion_refuses_an_unknown_output_type(tmp_path, project) -> None:
    import asyncio

    checkpoint = tmp_path / "outputs" / "full"
    checkpoint.mkdir(parents=True)
    (checkpoint / "config.json").write_text("{}", encoding="utf-8")

    async def collect():
        return [
            event
            async for event in convert.run_conversion(
                project, "outputs/full", out_type="q4_k_m"
            )
        ]

    events = asyncio.run(collect())
    # q4_k_m is a `llama-quantize` step, not something the converter writes —
    # offering it and silently producing f16 would be worse than refusing.
    assert "unsupported output type" in events[0]["error"]


def test_the_gguf_lands_in_the_managed_directory(
    tmp_path, project, monkeypatch
) -> None:
    """Managed and not the project directory: managed is the one the catalog
    scans, the disk budget counts, and the delete route may touch."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    from backend.modules.llamacpp import catalog

    out = convert._output_path(project, tmp_path / "checkpoint-200", "model", "f16")
    assert catalog.models_root() in out.parents
    assert out.name == "proj-checkpoint-200-f16.gguf"

    lora = convert._output_path(project, tmp_path / "adapter", "lora", "f16")
    assert lora.name.endswith("-lora-f16.gguf")


def test_warmup_ratio_and_warmup_steps_are_not_aliases() -> None:
    """transformers 5 dropped `warmup_ratio` and kept `warmup_steps`.

    Treating them as the same field would send `0.03` — three percent of
    training — into a field meaning "three hundredths of a step", which is no
    warmup at all and raises nothing anywhere. So they are two fields, and on any
    given version one of them shows as dropped.
    """
    ratio = next(f for f in recipes.FIELDS if f.name == "warmup_ratio")
    steps = next(f for f in recipes.FIELDS if f.name == "warmup_steps")
    assert "warmup_steps" not in ratio.aliases
    assert "warmup_ratio" not in steps.aliases

    # transformers 5: only `warmup_steps` exists.
    modern = intro(sft=[n for n in all_names("sft") if n != "warmup_ratio"])
    code = code_of(recipes.materialize(recipes.Recipe(), modern))
    assert "warmup_steps=" in code
    assert "warmup_ratio=" not in code
