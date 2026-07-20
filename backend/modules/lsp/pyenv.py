"""Resolve the Python interpreter basedpyright should analyze against for a file.

stdlib (`os.`, …) resolves from basedpyright's bundled typeshed, but third-party
imports (torch, numpy, …) only resolve when the server is pointed at a real
interpreter whose site-packages holds them. The editor client hands this path to
basedpyright as `python.pythonPath` in its `workspace/configuration` answer (see
editor/lsp.ts); the backend just discovers it (the `lsp` pipe stays LSP-agnostic).

Resolution order:
1. The nearest `.venv`/`venv` walking up from the file — the app's own venv
   convention (see training/envs.py `python_path`), so venv projects "just work".
2. Else the system default interpreter, deliberately **not** the backend's own venv
   (which only carries the dashboard's deps): the `py` launcher default on Windows,
   else `python3`/`python` on PATH — skipping our own `sys.executable` either way.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

# Import name (what the user types / the curated registry uses) → pip distribution
# name, for the framework packages we track. Most match; the exceptions are the point.
FRAMEWORK_PACKAGES: dict[str, str] = {
    # Scientific / numeric core.
    "numpy": "numpy",
    "pandas": "pandas",
    "scipy": "scipy",
    "sympy": "sympy",
    "sklearn": "scikit-learn",
    "statsmodels": "statsmodels",
    "polars": "polars",
    "pyarrow": "pyarrow",
    # Plotting.
    "matplotlib": "matplotlib",
    "seaborn": "seaborn",
    "plotly": "plotly",
    # Deep learning.
    "torch": "torch",
    "torchvision": "torchvision",
    "einops": "einops",
    # Hugging Face + fine-tuning / serving.
    "transformers": "transformers",
    "datasets": "datasets",
    "tokenizers": "tokenizers",
    "accelerate": "accelerate",
    "peft": "peft",
    "safetensors": "safetensors",
    "huggingface_hub": "huggingface-hub",
    "sentence_transformers": "sentence-transformers",
    "trl": "trl",
    "bitsandbytes": "bitsandbytes",
    "vllm": "vllm",
    # LLM API clients + orchestration.
    "openai": "openai",
    "anthropic": "anthropic",
    "google.genai": "google-genai",
    "langchain": "langchain",
    "langchain_core": "langchain-core",
    "langgraph": "langgraph",
    "litellm": "litellm",
    "ollama": "ollama",
    "tiktoken": "tiktoken",
    # RL / imaging / vector stores.
    "gymnasium": "gymnasium",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "lancedb": "lancedb",
    "chromadb": "chromadb",
    "faiss": "faiss-cpu",
    # Web / data plumbing the user's own buffers reach for constantly.
    "fastapi": "fastapi",
    "pydantic": "pydantic",
    "httpx": "httpx",
    "requests": "requests",
    "sqlalchemy": "SQLAlchemy",
}

# Ancestor files that mark a project root (checked alongside `.git`/`.venv`/`venv`).
_PROJECT_MARKERS = ("pyrightconfig.json", "pyproject.toml", "setup.py", "setup.cfg")


def _venv_python(venv: Path) -> Path:
    """The interpreter path inside a venv directory, per-platform (mirrors
    training/envs.py so both modules agree on the layout)."""
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _nearest_venv_python(start: Path) -> str | None:
    """The first `.venv`/`venv` interpreter found walking up from `start`."""
    for directory in (start, *start.parents):
        for name in (".venv", "venv"):
            candidate = _venv_python(directory / name)
            if candidate.is_file():
                return str(candidate)
    return None


def _clean_path() -> str:
    """PATH with the backend's own venv scrubbed out. The backend usually runs inside
    its venv (`VIRTUAL_ENV` set, `<venv>/Scripts` on PATH), which would make every
    interpreter probe resolve back to *our* Python — the one without the user's
    packages. Dropping those entries yields what the user gets in a plain shell."""
    venv = os.environ.get("VIRTUAL_ENV", "")
    path = os.environ.get("PATH", "")
    if not venv:
        return path
    low = venv.lower()
    return os.pathsep.join(p for p in path.split(os.pathsep) if low not in p.lower())


@lru_cache(maxsize=1)
def _system_python() -> str | None:
    """The user's default interpreter — never the backend's own venv. Cached: it's
    stable for the process, and the Windows fallback shells out to the `py` launcher.

    Prefers `python`/`python3` on the (venv-scrubbed) PATH — what the user's shell
    resolves — over the `py` launcher default, which reports the *newest installed*
    interpreter rather than the one on PATH."""
    own = Path(sys.executable).resolve()
    path = _clean_path()
    for name in ("python", "python3"):
        exe = shutil.which(name, path=path)
        if exe is not None and Path(exe).resolve() != own:
            return exe
    if sys.platform == "win32":
        launcher = shutil.which("py")
        if launcher is not None:
            env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
            env["PATH"] = path
            try:
                out = subprocess.run(
                    [launcher, "-c", "import sys;print(sys.executable)"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                    env=env,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                out = ""
            if out and Path(out).resolve() != own:
                return out
    return None


def resolve_python_interpreter(start_dir: str | None) -> str | None:
    """The interpreter to analyze a file under `start_dir` with, or None to let
    basedpyright use its own default. `start_dir` is a directory (a file's path is
    tolerated — its parent is used)."""
    if not start_dir:
        return _system_python()
    base = Path(start_dir)
    start = base if base.is_dir() else base.parent
    return _nearest_venv_python(start) or _system_python()


def resolve_project_root(start_dir: str | None) -> str | None:
    """The project root for a file: the nearest ancestor holding a project marker
    (`pyrightconfig.json`, `pyproject.toml`, `setup.py`/`.cfg`, `.git`, or a
    `.venv`/`venv`), else `start_dir` itself. Anchors the LSP `rootUri` and the
    per-project server pool, so one server serves the whole project instead of one
    per directory."""
    if not start_dir:
        return None
    base = Path(start_dir)
    start = base if base.is_dir() else base.parent
    for directory in (start, *start.parents):
        if any((directory / marker).exists() for marker in _PROJECT_MARKERS):
            return str(directory)
        if (
            (directory / ".git").exists()
            or (directory / ".venv").is_dir()
            or (directory / "venv").is_dir()
        ):
            return str(directory)
    return str(start)


@lru_cache(maxsize=16)
def installed_versions(interpreter: str | None) -> dict[str, str]:
    """Installed pip versions of the framework packages in `interpreter`'s environment
    (`{dist_name: version}`, only installed ones present). Runs the interpreter once via
    `importlib.metadata`; cached per interpreter (versions are stable until reinstall).
    Empty when there's no interpreter or the probe fails."""
    if not interpreter:
        return {}
    dists = sorted(set(FRAMEWORK_PACKAGES.values()))
    code = (
        "import importlib.metadata as m, json\n"
        f"dists = {dists!r}\n"
        "out = {}\n"
        "for d in dists:\n"
        "    try:\n"
        "        out[d] = m.version(d)\n"
        "    except Exception:\n"
        "        pass\n"
        "print(json.dumps(out))\n"
    )
    try:
        proc = subprocess.run(
            [interpreter, "-c", code],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        data = json.loads(proc.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()}
