"""llama.cpp as a first-class local chat provider.

Ollama and LM Studio are *applications* the user installs and manages elsewhere;
this module makes the node itself able to serve a GGUF. It owns three things:

- **the binary** — an upstream `llama-server` release downloaded on demand into
  `$HORRIBLE_DATA_DIR/llamacpp/bin/<tag>-<variant>/` and hash-verified
  (`binaries.py`), the geoip-mmdb / `playwright install chromium` precedent;
- **the weights** — a GGUF catalog spanning our own managed directory and the
  files Ollama and LM Studio already have on disk, plus Hugging Face downloads
  with a disk budget (`catalog.py`);
- **the process** — one supervised `llama-server` with a health gate, so the
  agent only ever talks to a server that has finished loading (`server.py`).

Chat integration is deliberately *not* a new dialect: `llama-server` speaks the
OpenAI API, so it is one `PROVIDERS` entry with `dialect="openai"` and everything
already built on that dialect — streamed reasoning, tool-call assembly, the
`tool_choice="required"` retry — works unchanged. See docs/modules/llamacpp.mdx.
"""

from backend.modules.llamacpp.routes import router

__all__ = ["router"]
