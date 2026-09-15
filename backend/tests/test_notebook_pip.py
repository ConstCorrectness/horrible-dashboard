"""`!pip` / `%pip` in notebook cells.

Two halves: the IPython extension every kernel loads (`kernel_ext/horrible_pip.py`),
which routes installs through uv into the kernel's own interpreter because a uv venv
has no pip; and the reactive analysis, which must read IPython syntax as a cell
rather than a syntax error.
"""

from __future__ import annotations

import os
import sys

import pytest
from IPython.core.error import UsageError
from IPython.core.inputtransformer2 import TransformerManager

from backend.notebook_core.kernel_ext import horrible_pip as hp
from backend.notebook_core.reactive import ReactiveGraph, analyze
from backend.notebook_core.session import (
    KERNEL_EXT_DIR,
    _kernel_env,
    _make_kernel_manager,
)

# --- reactive analysis ------------------------------------------------------


def test_bang_pip_above_import_still_defines_the_import():
    a = analyze("!pip install regex\nimport regex")
    assert a.parse_error is None
    assert a.defs == {"regex"}


def test_captured_shell_output_is_a_def():
    a = analyze("files = !ls\nn = len(files)")
    assert a.parse_error is None
    assert a.defs == {"files", "n"}


def test_line_magic_inside_a_block_keeps_indentation():
    a = analyze("if flag:\n    %time x = 1\n    y = 2")
    assert a.parse_error is None
    assert a.defs == {"y"}
    assert a.refs == {"flag"}


def test_python_cell_magic_body_is_analyzed():
    a = analyze("%%time\nx = y + 1")
    assert a.parse_error is None
    assert a.defs == {"x"}
    assert a.refs == {"y"}


def test_non_python_cell_magic_is_opaque_not_broken():
    a = analyze("%%bash\nls -la | wc -l")
    assert a.parse_error is None
    assert a.defs == set()


def test_valid_python_is_never_rewritten():
    # A `!` at line start inside a string must survive: stripping only runs on failure.
    a = analyze('s = """\n!not a command\n"""\nt = s')
    assert a.defs == {"s", "t"}


def test_real_syntax_error_next_to_magic_is_still_reported():
    assert analyze("!pip install x\ndef (").parse_error is not None


def test_graph_edge_from_install_cell_to_user():
    g = ReactiveGraph.build(
        [("a", "!pip install regex\nimport regex"), ("b", "p = regex.compile('x')")]
    )
    assert not g.diagnostics
    assert g.edges["a"] == {"b"}


# --- input transformer --------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("!pip install regex\n", "%pip install regex\n"),
        ("  !pip3 install -U regex", "  %pip install -U regex"),
        ("!python -m pip install regex", "%pip install regex"),
        ("! uv pip install regex", "%pip install regex"),
        ("!pip", "%pip"),
    ],
)
def test_bang_pip_spellings_rewrite_to_the_magic(line, expected):
    assert hp.rewrite_lines([line]) == [expected]


@pytest.mark.parametrize("line", ["!pipx install x", "!ls -la", "x = 1", "# !pip x"])
def test_other_lines_are_untouched(line):
    assert hp.rewrite_lines([line]) == [line]


def test_transformer_hands_the_line_to_the_pip_magic():
    tm = TransformerManager()
    tm.cleanup_transforms.append(hp.rewrite_lines)
    assert "run_line_magic('pip', 'install regex')" in tm.transform_cell(
        "!pip install regex\n"
    )
    assert "system('echo hi')" in tm.transform_cell("!echo hi\n")


def test_extension_registers_magics_and_transformer_once():
    class Shell:
        def __init__(self) -> None:
            self.magics: dict[str, object] = {}
            self.input_transformers_cleanup: list[object] = []

        def register_magic_function(self, fn, magic_kind, magic_name):
            assert magic_kind == "line"
            self.magics[magic_name] = fn

    shell = Shell()
    hp.load_ipython_extension(shell)
    hp.load_ipython_extension(shell)  # %reload_ext must not stack transformers
    assert shell.magics == {"pip": hp.pip_magic, "uv": hp.uv_magic}
    assert shell.input_transformers_cleanup == [hp.rewrite_lines]


# --- command building ---------------------------------------------------------


def test_uv_targets_the_kernel_interpreter():
    assert hp.build_command(["install", "regex"], uv="uv", python="py") == [
        "uv",
        "pip",
        "install",
        "--python",
        "py",
        "regex",
    ]


def test_pip_only_flags_are_translated_for_uv():
    cmd = hp.build_command(["uninstall", "-y", "regex"], uv="uv", python="py")
    assert "-y" not in cmd and cmd[-1] == "regex"
    cmd = hp.build_command(["install", "--no-cache-dir", "x"], uv="uv", python="py")
    assert "--no-cache" in cmd and "--no-cache-dir" not in cmd


def test_without_uv_falls_back_to_pip_verbatim():
    assert hp.build_command(["uninstall", "-y", "x"], uv=None, python="py") == [
        "py",
        "-m",
        "pip",
        "uninstall",
        "-y",
        "x",
    ]


@pytest.mark.parametrize("args", [[], ["config", "list"], ["download", "x"]])
def test_unsupported_subcommands_are_refused(args):
    with pytest.raises(ValueError):
        hp.build_command(args, uv="uv", python="py")


def test_requested_names_skip_options_paths_and_urls():
    args = [
        "install",
        "-U",
        "Regex",
        "-r",
        "req.txt",
        "scikit_learn==1.5",
        "./local",
        "git+https://example.com/x",
        "--index-url=https://idx",
    ]
    assert hp.requested_names(args) == {"regex", "scikit-learn"}


def test_stale_modules_maps_distribution_to_imported_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "fakemod_hd", object())
    monkeypatch.setattr(
        hp.importlib.metadata,
        "packages_distributions",
        lambda: {"fakemod_hd": ["fake-dist-hd"]},
    )
    assert hp.stale_modules({"fake-dist-hd"}) == ["fakemod_hd"]
    assert hp.stale_modules({"not-imported"}) == []


# --- the magic ----------------------------------------------------------------


def test_failed_install_fails_the_cell(monkeypatch):
    monkeypatch.setattr(hp, "resolve_uv", lambda: "uv")
    monkeypatch.setattr(hp, "_run", lambda cmd: 2)
    with pytest.raises(UsageError, match="exited with 2"):
        hp.pip_magic("install regex")


def test_upgrading_an_imported_package_says_restart(monkeypatch, capsys):
    ran: list[list[str]] = []
    monkeypatch.setattr(hp, "resolve_uv", lambda: "uv")
    monkeypatch.setattr(hp, "_run", lambda cmd: ran.append(cmd) or 0)
    monkeypatch.setattr(hp, "stale_modules", lambda names: ["regex"])
    hp.pip_magic("install -U regex")
    assert ran and ran[0][:5] == ["uv", "pip", "install", "--python", sys.executable]
    assert "Restart the kernel" in capsys.readouterr().out


def test_uv_magic_requires_pip(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(hp, "pip_magic", seen.append)
    hp.uv_magic("pip install regex")
    assert seen == ["install regex"]
    with pytest.raises(UsageError):
        hp.uv_magic("venv .venv")


# --- kernel wiring ------------------------------------------------------------


def test_kernel_env_puts_the_extension_on_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "existing")
    monkeypatch.delenv("HORRIBLE_UV", raising=False)
    monkeypatch.setattr("backend.notebook_core.session.shutil.which", lambda _: "/x/uv")
    env = _kernel_env({"EXTRA": "1"})
    assert env["PYTHONPATH"] == os.pathsep.join([str(KERNEL_EXT_DIR), "existing"])
    assert env["HORRIBLE_UV"] == "/x/uv"
    assert env["EXTRA"] == "1"
    assert (KERNEL_EXT_DIR / "horrible_pip.py").is_file()


def test_kernelspec_loads_the_extension():
    argv = _make_kernel_manager("py", "notebook").kernel_spec.argv
    assert "--IPKernelApp.extensions=horrible_pip" in argv
