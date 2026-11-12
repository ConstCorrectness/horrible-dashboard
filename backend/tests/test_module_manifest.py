"""The horrible-module.json contract: parsing, legacy plugins, discovery, load order."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from backend.sdk.module_manifest import ManifestError, parse_manifest, read_manifest
from backend.sdk.module_scan import load_order, scan_modules

BASE = {"id": "demo", "name": "Demo", "version": "1.0.0", "sdkVersion": 4}


def _write(dir_: Path, name: str, data: dict) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / name).write_text(json.dumps(data), encoding="utf-8")
    return dir_


def test_full_manifest_round_trips_to_camel_case() -> None:
    m = parse_manifest(
        {
            **BASE,
            "web": {"entry": "@horrible-module/demo"},
            "py": {
                "root": "py/src",
                "package": "horrible_mod_demo",
                "entry": "horrible_mod_demo:MODULE",
            },
            "dependsOn": {"audio": "^1"},
            "provides": ["demo.thing@1"],
            "requires": ["audio.mediaStrip@1"],
            "toolGroups": [{"id": "demo", "description": "Demo tools"}],
            "standalone": {"pane": "demo.main"},
        }
    )
    assert m.owns_tool("demo.play") and not m.owns_tool("other.play")
    wire = m.to_json()
    assert wire["sdkVersion"] == 4
    assert wire["toolGroups"] == [
        {"id": "demo", "description": "Demo tools", "alwaysOn": False}
    ]
    assert wire["standalone"] == {"pane": "demo.main", "port": 0}
    assert parse_manifest(wire) == m


def test_legacy_plugin_manifest_becomes_a_web_dist() -> None:
    m = parse_manifest(
        {
            "id": "hello-widget",
            "name": "Hello",
            "version": "0.1.0",
            "entry": "dist/index.js",
            "sdkVersion": 3,
        }
    )
    assert m.web is not None and m.web.dist == "dist/index.js"
    assert m.py is None


@pytest.mark.parametrize(
    ("patch", "needle"),
    [
        ({"id": "Bad Id"}, "id"),
        ({"sdkVersion": 2}, "SDK v2"),
        ({"sdkVersion": 99}, "SDK v99"),
        ({"provides": ["no-version"]}, "provides"),
        ({"py": {"root": "py", "package": "x", "entry": "nocolon"}}, "py.entry"),
        ({"standalone": {}}, "standalone.pane"),
    ],
)
def test_invalid_manifests_name_the_field(patch: dict, needle: str) -> None:
    with pytest.raises(ManifestError, match=needle):
        parse_manifest({**BASE, **patch})


def test_id_must_equal_directory(tmp_path: Path) -> None:
    pkg = _write(tmp_path / "elsewhere", "horrible-module.json", BASE)
    with pytest.raises(ManifestError, match="directory name"):
        read_manifest(pkg)


def test_scan_prefers_builtin_and_records_bad_packages(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    installed = tmp_path / "installed"
    _write(builtin / "demo", "horrible-module.json", BASE)
    _write(installed / "demo", "horrible-module.json", {**BASE, "name": "Shadow"})
    _write(
        installed / "broken",
        "horrible-module.json",
        {**BASE, "id": "broken", "sdkVersion": 1},
    )
    (installed / "not-a-package").mkdir()

    result = scan_modules(
        builtin_dir=builtin, installed_dir=installed, entry_points=False
    )
    assert result.modules["demo"].manifest.name == "Demo"
    assert result.modules["demo"].builtin
    assert "broken" not in result.modules
    assert any("broken" in where for where, _ in result.errors)


def test_python_half_imports_from_its_root_without_pip_install(tmp_path: Path) -> None:
    pkg = tmp_path / "mods" / "demo"
    src = pkg / "py" / "src" / "horrible_mod_scan_demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("MODULE = 'loaded'\n", encoding="utf-8")
    _write(
        pkg,
        "horrible-module.json",
        {
            **BASE,
            "py": {
                "root": "py/src",
                "package": "horrible_mod_scan_demo",
                "entry": "horrible_mod_scan_demo:MODULE",
            },
        },
    )
    result = scan_modules(
        builtin_dir=tmp_path / "mods",
        installed_dir=tmp_path / "none",
        entry_points=False,
    )
    try:
        assert result.modules["demo"].load_python() == "loaded"
    finally:
        sys.modules.pop("horrible_mod_scan_demo", None)
        sys.path[:] = [p for p in sys.path if not p.endswith(str(Path("py") / "src"))]


def _m(mid: str, **kw):
    return parse_manifest({**BASE, "id": mid, **kw})


def test_load_order_follows_depends_on_and_contracts() -> None:
    order, problems = load_order(
        [
            _m("karaoke", requires=["audio.mediaStrip@1"]),
            _m("ide", dependsOn={"editor": "*"}),
            _m("editor"),
            _m("audio", provides=["audio.mediaStrip@1"]),
        ]
    )
    assert problems == []
    assert order.index("audio") < order.index("karaoke")
    assert order.index("editor") < order.index("ide")


def test_load_order_reports_cycles_and_missing_but_still_loads_everything() -> None:
    order, problems = load_order(
        [
            _m("people", dependsOn={"social": "*"}),
            _m("social", dependsOn={"people": "*"}),
            _m("x", dependsOn={"ghost": "*"}),
        ]
    )
    assert sorted(order) == ["people", "social", "x"]
    assert any("cycle" in p for p in problems)
    assert any("ghost" in p for p in problems)
