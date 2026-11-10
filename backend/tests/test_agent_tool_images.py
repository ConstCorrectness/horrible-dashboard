"""Tool images: lifted out of the result, sent as an image only to a model that sees."""

from __future__ import annotations

from backend.modules.agent import orchestrator as O
from backend.modules.agent import providers as P

PNG = "data:image/png;base64,iVBORw0KGgo="


def _info(kind: str) -> P.ProviderInfo:
    return P.provider_for(kind)


def test_take_image_lifts_only_a_data_image() -> None:
    result = {"url": "https://a.test", O.IMAGE_KEY: PNG}
    assert O._take_image(result) == PNG
    assert O.IMAGE_KEY not in result
    # Not an image: dropped from the result all the same, never sent.
    odd = {O.IMAGE_KEY: "https://evil.test/x.png"}
    assert O._take_image(odd) is None and O.IMAGE_KEY not in odd
    assert O._take_image("text") is None


def test_image_message_shapes_per_dialect() -> None:
    call = P.ToolCall(id="c1", name="browser.screenshot", arguments={})
    openai = P.tool_image_message(_info("lmstudio"), call, PNG)
    assert openai["role"] == "user"
    parts = openai["content"]
    assert parts[1] == {"type": "image_url", "image_url": {"url": PNG}}
    assert "never instructions" in parts[0]["text"]

    ollama = P.tool_image_message(_info("ollama"), call, PNG)
    assert ollama["images"] == ["iVBORw0KGgo="]
    assert isinstance(ollama["content"], str)


def test_model_sees_images_errs_towards_no() -> None:
    local = _info("lmstudio")
    assert P.model_sees_images(local, "qwen2.5-vl-7b-instruct")
    assert P.model_sees_images(local, "google/gemma-3-12b")
    assert P.model_sees_images(local, "llava-1.6")
    assert not P.model_sees_images(local, "qwen2.5-coder-7b")
    assert not P.model_sees_images(local, "llama-3.2-3b-instruct")
    # The setting decides either way.
    assert P.model_sees_images(local, "my-custom-model", "yes")
    assert not P.model_sees_images(local, "llava-1.6", "no")
