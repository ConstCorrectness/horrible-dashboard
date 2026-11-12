"""Module packages load and unload in a running app: routes, tools, tasks, hooks."""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.kernel.module_host import ModuleError
from backend.kernel.module_manager import ModuleManager
from backend.sdk.registry import PluginRegistry

MODULE_SRC = """
import asyncio
from fastapi import APIRouter
from backend.kernel import AgentTool, HorribleModule

EVENTS = []
router = APIRouter(prefix="/{mid}")


@router.get("/ping")
def ping():
    return {{"pong": "{mid}"}}


async def _beat():
    EVENTS.append("task-start")
    try:
        await asyncio.sleep(3600)
    finally:
        EVENTS.append("task-stop")


class Mod(HorribleModule):
    def setup(self, host):
        host.add_router(router)
        host.add_agent_tool(AgentTool(name="{mid}.hello", description="hi", handler=lambda a: "hi"))
        host.on_startup(lambda: EVENTS.append("startup"))
        host.on_shutdown(lambda: EVENTS.append("shutdown"))
        host.background(_beat)
        host.provide("{mid}.thing@1", {{"n": 1}})


MODULE = Mod()
"""


def _package(root: Path, mid: str, **manifest) -> Path:
    pkg = root / mid
    pkg_name = f"horrible_mod_test_{mid.replace('-', '_')}"
    src = pkg / "py" / "src" / pkg_name
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(
        textwrap.dedent(MODULE_SRC.format(mid=mid)), encoding="utf-8"
    )
    (pkg / "horrible-module.json").write_text(
        json.dumps(
            {
                "id": mid,
                "name": mid.title(),
                "version": "1.0.0",
                "sdkVersion": 4,
                "py": {
                    "root": "py/src",
                    "package": pkg_name,
                    "entry": f"{pkg_name}:MODULE",
                },
                "toolGroups": [{"id": mid, "description": f"{mid} tools"}],
                "provides": [f"{mid}.thing@1"],
                **manifest,
            }
        ),
        encoding="utf-8",
    )
    return pkg


@pytest.fixture()
def env(tmp_path: Path):
    builtin = tmp_path / "modules"
    builtin.mkdir()
    app = FastAPI()
    reg = PluginRegistry()
    mgr = ModuleManager(reg)
    mgr.scan_kwargs = {
        "builtin_dir": builtin,
        "installed_dir": tmp_path / "installed",
        "entry_points": False,
    }
    mgr.attach(app)
    yield builtin, app, reg, mgr
    for name in [m for m in sys.modules if m.startswith("horrible_mod_test_")]:
        sys.modules.pop(name, None)


def _events(mid: str) -> list[str]:
    return sys.modules[f"horrible_mod_test_{mid}"].EVENTS


def test_boot_load_serves_routes_and_groups_tools(env) -> None:
    builtin, app, reg, mgr = env
    _package(builtin, "alpha")
    mgr.discover()
    mgr.load_all()
    assert TestClient(app).get("/api/alpha/ping").json() == {"pong": "alpha"}
    # A module tool is never silently always-on: it lands in its group.
    assert reg.agent_tools["alpha.hello"].group == "alpha"
    assert reg.service("alpha.thing@1") == {"n": 1}
    assert mgr.describe("alpha")["loaded"] is True
    # The agent's group catalog reads the manifest's blurb, and only while loaded.
    assert mgr.tool_group_description("alpha") == "alpha tools"
    assert mgr.tool_group_description("nobody") is None


def test_hot_unload_and_reload(env) -> None:
    builtin, app, reg, mgr = env
    _package(builtin, "beta")
    mgr.discover()
    client = TestClient(app)

    async def scenario() -> None:
        await mgr.start()  # the app is "running": loads now start tasks + hooks
        await mgr.load("beta")
        await asyncio.sleep(0)
        assert client.get("/api/beta/ping").status_code == 200
        assert _events("beta") == ["startup", "task-start"]

        await mgr.set_enabled("beta", False)
        assert client.get("/api/beta/ping").status_code == 404
        assert "beta.hello" not in reg.agent_tools
        assert reg.service("beta.thing@1") is None
        assert reg.owned.get("beta") is None
        assert _events("beta")[-2:] == ["task-stop", "shutdown"]
        assert mgr.disabled() == {"beta"}

        await mgr.set_enabled("beta", True)
        await asyncio.sleep(0)
        assert client.get("/api/beta/ping").status_code == 200
        assert "beta.hello" in reg.agent_tools
        await mgr.stop()
        assert _events("beta")[-1] == "shutdown"

    asyncio.run(scenario())


def test_route_collision_refuses_the_whole_module(env) -> None:
    builtin, app, reg, mgr = env

    @app.get("/api/gamma/ping")
    def taken() -> dict:
        return {}

    _package(builtin, "gamma")
    mgr.discover()
    mgr.load_all()
    assert "already served" in (mgr.describe("gamma")["error"] or "")
    assert "gamma.hello" not in reg.agent_tools  # nothing half-registered


def test_tool_outside_declared_groups_fails_load(env) -> None:
    builtin, app, reg, mgr = env
    _package(builtin, "delta", toolGroups=[{"id": "other", "description": "x"}])
    mgr.discover()
    mgr.load_all()
    assert "outside its declared toolGroups" in (mgr.describe("delta")["error"] or "")
    assert reg.owned.get("delta") is None


def test_dependency_order_and_refusals(env) -> None:
    builtin, app, reg, mgr = env
    _package(builtin, "base")
    _package(builtin, "top", dependsOn={"base": "^1"})
    mgr.discover()
    mgr.load_all()
    assert mgr.is_loaded("base") and mgr.is_loaded("top")

    async def scenario() -> None:
        with pytest.raises(ModuleError, match="depend"):
            await mgr.set_enabled("base", False)
        await mgr.set_enabled("top", False)
        await mgr.set_enabled("base", False)
        with pytest.raises(ModuleError, match="enable base first"):
            await mgr.set_enabled("top", True)

    asyncio.run(scenario())


def test_install_and_uninstall_a_third_party_package(env, tmp_path: Path) -> None:
    builtin, app, reg, mgr = env
    catalog = tmp_path / "catalog"
    _package(catalog, "eps")
    mgr.discover()

    async def scenario() -> None:
        desc = await mgr.install(catalog / "eps")
        assert desc["source"] == "installed" and desc["loaded"] is True
        assert TestClient(app).get("/api/eps/ping").status_code == 200
        await mgr.uninstall("eps")
        assert TestClient(app).get("/api/eps/ping").status_code == 404
        assert not (tmp_path / "installed" / "eps").exists()

    asyncio.run(scenario())
