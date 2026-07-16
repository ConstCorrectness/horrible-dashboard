"""CLIP image/text encoder — the library's *visual* search space.

The app's embedder (`database/embeddings.py`) is text-only, so an image is only
findable through the words that happen to sit around it (alt, caption, heading). That
leaves two holes this module fills: an image with **no** describing text can't be
found at all, and "the diagram that *looks* like X" isn't expressible. CLIP puts
images and text in one 512-dim space, so a text query can match pixels directly.

Design notes:

- **ONNX Runtime, not torch.** The runtime is ~60 MB against torch's ~1-2 GB, and
  `pillow` / `numpy` / `huggingface-hub` are already core deps — so the `clip` extra
  stays installable on every OS (the same reasoning as the `browser-engine` and
  `games-native` extras). Weights download once, ~350 MB, pinned by revision.
- **Two graphs, not one.** `Xenova/clip-vit-base-patch32` exports `text_model` and
  `vision_model` separately; the combined `model.onnx` demands `input_ids` *and*
  `pixel_values` in one call, which is useless when encoding a lone image.
- **A dedicated single-worker executor**, not `asyncio.to_thread`. The default
  executor is shared process-wide and capped at `min(32, cpu+4)`; long CLIP calls
  would starve the other `to_thread` users (SSRF DNS validation in `browser/fetch.py`,
  LSP, DB drivers). One worker also serializes inference, which we want anyway.
- **Preprocessing must match the reference exactly** (bicubic shortest-edge resize →
  center crop → CLIP's own mean/std). Get it subtly wrong and you don't get an error,
  you get quietly bad vectors — the worst failure mode here.

Vectors are L2-normalized, so LanceDB's cosine metric behaves.
See docs/modules/library.mdx.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

# Pinned: a silent re-export upstream would change the vector space under our stored
# embeddings, and nothing would look broken — searches would just quietly get worse.
MODEL_REPO = "Xenova/clip-vit-base-patch32"
MODEL_REVISION = "d15189d7028b43f1d3e65039190477f6af591c2a"
CLIP_DIM = 512

# From the repo's preprocessor_config.json. resample=3 is PIL BICUBIC.
_IMAGE_SIZE = 224
_IMAGE_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_IMAGE_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

# CLIP's text encoder is trained at 77 tokens; anything longer is truncated. This is
# why CLIP only ever encodes *queries* here, never document text — the app embedder
# owns long-form text.
_MAX_TOKENS = 77

# Decoding attacker-supplied bytes is its own sink. Pillow's own bomb guard sits near
# 89M pixels; a real photo worth embedding is far under this.
_MAX_PIXELS = 50_000_000


def clip_installed() -> bool:
    """True when the `clip` extra is importable (`uv sync --extra clip`)."""
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def clip_enabled() -> bool:
    """True when visual search is both switched on and actually installed."""
    return bool(get_value("library.clipEnabled", False)) and clip_installed()


class _ClipEncoder:
    """Lazily-loaded CLIP sessions, driven from one dedicated thread.

    Process-global (mirroring `code/semantic.py`'s index singleton): the sessions are
    hundreds of MB and take seconds to construct, so they load once, on first use —
    never at import, since `backend/app.py`'s import graph is already heavy enough.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False
        self._text_session: Any = None
        self._vision_session: Any = None
        self._tokenizer: Any = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip")

    def _load(self) -> None:
        """Build the sessions. Runs on the pool thread, guarded by `_lock`."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:  # another call won the race while we waited
                return
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer

            def fetch(name: str) -> str:
                return hf_hub_download(MODEL_REPO, name, revision=MODEL_REVISION)

            logger.info("loading CLIP (%s) — first run downloads ~350 MB", MODEL_REPO)
            opts = ort.SessionOptions()
            # We already serialize on one worker; let ORT use the cores it wants
            # within a call rather than fighting our own pool for them.
            self._text_session = ort.InferenceSession(
                fetch("onnx/text_model.onnx"),
                opts,
                providers=["CPUExecutionProvider"],
            )
            self._vision_session = ort.InferenceSession(
                fetch("onnx/vision_model.onnx"),
                opts,
                providers=["CPUExecutionProvider"],
            )
            self._tokenizer = Tokenizer.from_file(fetch("tokenizer.json"))
            self._tokenizer.enable_truncation(max_length=_MAX_TOKENS)
            self._loaded = True
            logger.info("CLIP ready (%d-dim)", CLIP_DIM)

    # ---- encoding (pool thread) --------------------------------------------

    def _encode_text(self, text: str) -> list[float]:
        self._load()
        ids = self._tokenizer.encode(text).ids
        arr = np.array([ids], dtype=np.int64)
        out = self._text_session.run(["text_embeds"], {"input_ids": arr})[0]
        return _l2(out[0])

    def _encode_image(self, raw: bytes) -> list[float]:
        self._load()
        pixels = _preprocess(raw)
        out = self._vision_session.run(["image_embeds"], {"pixel_values": pixels})[0]
        return _l2(out[0])

    # ---- event-loop side ---------------------------------------------------

    async def encode_text(self, text: str) -> list[float]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._encode_text, text)

    async def encode_image(self, raw: bytes) -> list[float]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._encode_image, raw)


def _l2(vec: Any) -> list[float]:
    """L2-normalize so cosine similarity is a plain dot product."""
    arr = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return [float(x) for x in arr]


def _preprocess(raw: bytes) -> Any:
    """Bytes → CLIP's expected `pixel_values` (1, 3, 224, 224) float32.

    Mirrors the reference CLIPImageProcessor: RGB → bicubic resize of the shortest
    edge to 224 → center crop → scale to [0,1] → normalize by CLIP's mean/std.
    """
    import io

    from PIL import Image

    with Image.open(io.BytesIO(raw)) as img:
        # `open` is lazy — check dimensions before anything forces a full decode.
        width, height = img.size
        if width * height > _MAX_PIXELS:
            raise ValueError(f"image too large to decode: {width}x{height}")
        img = img.convert("RGB")

        scale = _IMAGE_SIZE / min(width, height)
        img = img.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.BICUBIC,
        )
        left = (img.width - _IMAGE_SIZE) // 2
        top = (img.height - _IMAGE_SIZE) // 2
        img = img.crop((left, top, left + _IMAGE_SIZE, top + _IMAGE_SIZE))
        arr = np.asarray(img, dtype=np.float32) / 255.0

    arr = (arr - _IMAGE_MEAN) / _IMAGE_STD
    return np.expand_dims(arr.transpose(2, 0, 1), axis=0)  # HWC → NCHW


encoder = _ClipEncoder()


async def encode_text(text: str) -> list[float]:
    """Encode a search query into CLIP's space (truncated at 77 tokens)."""
    return await encoder.encode_text(text)


async def encode_image(raw: bytes) -> list[float]:
    """Encode image bytes into CLIP's space."""
    return await encoder.encode_image(raw)
