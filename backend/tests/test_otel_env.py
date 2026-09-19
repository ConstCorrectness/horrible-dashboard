"""The OTLP variables injected into spawned kernels."""

from __future__ import annotations

from backend.modules.otel.env import otlp_env


def test_points_at_this_node_on_its_real_port(monkeypatch) -> None:
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_RESOURCE_ATTRIBUTES",
        "OTEL_SERVICE_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("backend.server_port.port", lambda: 8100)
    env = otlp_env(service="demo", dataset="notebooks, a=b")
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:8100/api/otel"
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert env["OTEL_SERVICE_NAME"] == "demo"
    # Percent-encoded: a comma or `=` in a dataset name must not split the list.
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == "horrible.dataset=notebooks%2C%20a%3Db"
    assert env["OTEL_METRICS_EXPORTER"] == "none"


def test_user_configured_exporter_wins(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    assert otlp_env(service="demo", dataset="x") == {}


def test_existing_resource_attributes_are_kept(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "team=ml")
    env = otlp_env(service="demo", dataset="x")
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == "team=ml,horrible.dataset=x"


def test_disabled_by_setting(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setattr("backend.modules.otel.env._enabled", lambda: False)
    assert otlp_env(service="demo", dataset="x") == {}
