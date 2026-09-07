# NVIDIA

Two capabilities behind one API key: the **NGC model catalog** and **NIM**,
NVIDIA's hosted inference.

## Getting a key

1. Sign in at <https://build.nvidia.com> (or ngc.nvidia.com).
2. Setup → **Generate API Key**. Personal keys start `nvapi-`.
3. Paste it into the NVIDIA tile on the home page.

The key is verified before the tile reports connected: a key that does not work is
worse than no key, because everything downstream then blames itself.

## What it unlocks

- `nvidia.searchModels` — search the NGC catalog.
- `nvidia.nimEndpoints` — list the models this key can call.
- **NIM as a chat or eval provider.** NIM speaks the OpenAI chat API, so
  `https://integrate.api.nvidia.com/v1` works anywhere this app takes an
  OpenAI-compatible endpoint — the chat provider setting, an eval target, or the
  datasets builder's `synthesize` step.
- **Nemo Data Designer** for synthetic dataset generation, if the `nemo` extra is
  installed (`uv sync --extra nemo`).

## What it is not

It is **not** a GPU. The hardware module probes the card in this machine with
`nvidia-smi` and has nothing to do with this connector; connecting NVIDIA here does
not make a local fine-tune faster.

## Cost

NIM calls are billed by NVIDIA against your account, including the ones the
datasets builder makes when its synthesize step is set to the `nemo` engine. The
`local` engine uses whichever model this node already serves and costs nothing.
