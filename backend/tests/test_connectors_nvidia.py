"""The NVIDIA connector: storing a key, and the provider that borrows it.

httpx is stubbed with MockTransport, so nothing here talks to NVIDIA.

The whole file exists because of one class of bug. This connector was written
against a **dict-shaped** credential store that does not exist — `store.save` takes
a `Credential` dataclass — so pasting a key raised
`TypeError: asdict() should be called on dataclass instances` before anything was
written, and `api_key()` would have failed the same way on the read side. Nothing
caught it because nothing here was tested.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from backend.modules.agent import providers as P
from backend.modules.connectors import store
from backend.modules.connectors.providers import nvidia


@pytest.fixture(autouse=True)
def clean():
    store.clear(nvidia.CONNECTOR_ID)
    yield
    store.clear(nvidia.CONNECTOR_ID)


def _mock_httpx(monkeypatch, handler):
    real_init = httpx.AsyncClient.__init__

    def init(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)


def _models_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer nvapi-test"
    return httpx.Response(
        200, json={"data": [{"id": "meta/llama-3.3-70b-instruct", "owned_by": "meta"}]}
    )


def test_submitting_a_key_stores_and_verifies_it(monkeypatch) -> None:
    """The exact path that raised the TypeError: paste a key, get connected."""
    _mock_httpx(monkeypatch, _models_handler)

    result: dict[str, Any] = asyncio.run(nvidia._submit({"api_key": "nvapi-test"}))

    assert result["connected"] is True
    assert nvidia.api_key() == "nvapi-test"
    cred = store.load(nvidia.CONNECTOR_ID)
    assert cred is not None and cred.access_token == "nvapi-test"
    assert nvidia._status().connected is True


def test_a_rejected_key_is_not_left_behind(monkeypatch) -> None:
    """A key that does not work is worse than no key, because everything downstream
    then blames itself — so a failed verification clears what it wrote."""
    _mock_httpx(monkeypatch, lambda r: httpx.Response(401, json={}))

    result = asyncio.run(nvidia._submit({"api_key": "nvapi-wrong"}))

    assert "error" in result
    assert nvidia.api_key() == ""
    assert store.load(nvidia.CONNECTOR_ID) is None


def test_a_blank_resubmit_keeps_the_stored_key(monkeypatch) -> None:
    """The form cannot prefill a secret, so blank is the default state of a field
    whose key is already stored — it means "leave it alone", never "clear it"."""
    _mock_httpx(monkeypatch, _models_handler)
    asyncio.run(nvidia._submit({"api_key": "nvapi-test"}))

    assert asyncio.run(nvidia._submit({"api_key": "  "}))["connected"] is True
    assert nvidia.api_key() == "nvapi-test"


def test_the_nim_provider_borrows_the_connector_key(monkeypatch) -> None:
    """One credential, two users: the agent's `nim` provider reads the connector's
    key rather than keeping a second copy under its own kind."""
    info = P.PROVIDERS["nim"]
    assert P.api_key_for(info) is None
    assert P.auth_headers(info) == {}

    _mock_httpx(monkeypatch, _models_handler)
    asyncio.run(nvidia._submit({"api_key": "nvapi-test"}))

    assert P.api_key_for(info) == "nvapi-test"
    assert P.auth_headers(info) == {"Authorization": "Bearer nvapi-test"}
