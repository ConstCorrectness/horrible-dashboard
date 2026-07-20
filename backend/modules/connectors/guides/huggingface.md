# Using the Hugging Face tools

The connected account's own models and datasets are reachable, private ones included.

## Pick the right tool

- Looking for a model to use? `huggingface.searchModels`, with `task` set when the user named one ("a speech model" → `automatic-speech-recognition`).
- Looking for training or eval data? `huggingface.searchDatasets`.
- "My models", "what have I published" → `huggingface.listRepos`.
- Know the repo but not the file layout? `huggingface.repoInfo` — it returns the file list, so you don't have to guess a path.
- Know the repo _and_ the path? `huggingface.readFile`.

## Repo ids and types

A repo id is always `owner/name` (`meta-llama/Llama-3-8B`), never a URL. Models and datasets are **separate namespaces** — `squad` the dataset and a model called `squad` are different repos. Every tool that takes a `repo` also takes `type`, defaulting to `model`; pass `type: "dataset"` or you will get a "not found" for a dataset that plainly exists.

## Reading files

`huggingface.readFile` is for **text**: `README.md` (the model card — usually what the user actually wants), `config.json`, `tokenizer_config.json`, dataset scripts. Weights (`.safetensors`, `.bin`, `.gguf`) are binary and are refused rather than returned as noise. Files are truncated at 100 KB; `truncated: true` says so.

The model card is the answer to most "what is this model / how do I use it / what licence" questions. Read it before speculating.

## Gated repos

Many popular models (Llama, Gemma, …) are **gated**: the user must accept a licence on the Hub before any token can read them. `repoInfo` reports `gated`, and a read attempt returns a "gated behind a licence" error. That is not something you can fix — tell the user to accept the terms on the model's Hub page, and stop.

## When a tool returns an error

- "isn't connected" / "rejected the stored token" → the user must connect or reconnect Hugging Face from the home page. You cannot fix this yourself; say so and stop. Setting it up needs an OAuth app: on huggingface.co, **Settings → Connected Apps → Developer Applications → Create App**, with no client secret. Give those clicks rather than a settings URL — HF's settings pages are auth-gated and a logged-out link just shows a login form.
- "gated behind a licence" → see above; the user accepts it on the Hub.
- "not found" → check whether you passed the right `type`. That's the usual cause.
