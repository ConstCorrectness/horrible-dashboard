"""What shape is this dataset in, and can the task I picked actually eat it?

A fine-tuning dataset is not one shape but a handful of conventions that look
alike from a distance and are incompatible up close. `{"instruction", "input",
"output"}` is Alpaca. `{"conversations": [{"from", "value"}]}` is ShareGPT.
`{"messages": [{"role", "content"}]}` is ChatML. `{"prompt", "chosen", "rejected"}`
is a preference set and is the *only* thing DPO or KTO can train on. A plain
`{"text"}` is raw corpus.

Until now the recipe form asked for a `text_field` and the user typed one in. That
question has three failure modes and all of them are silent:

- The named column does not exist. `trl` raises several minutes into the run, or —
  worse — finds *some* column and trains on the wrong one.
- The column exists but holds a nested structure. Training on `str(list_of_dicts)`
  produces a model that has learned to emit Python repr.
- The dataset is a preference set and the task is SFT (or the reverse). Nothing
  errors; the run simply optimises the wrong objective.

So detection is a *verdict with a reason*, never a silent coercion. `detect()`
scores every known shape against real rows and reports what it saw, so the pane can
say "ShareGPT — `conversations` holds from/value pairs in 3 of 3 rows" and the user
can overrule it with one click. `adapt()` then says whether that shape can feed a
given task, and what the column map has to be for it to work.

**A guess is never upgraded into a fact.** `Detection.confidence` below 0.5 renders
as "could not tell", and `adapt()` on an unknown format returns a refusal rather
than an optimistic mapping — the same posture as `recipes.resolve()`'s
`unsupported` status, and for the same reason: emitting something hopeful is how a
config error becomes a wasted GPU hour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Every shape we can recognise. `unknown` is a real member, not an error state —
#: a dataset we cannot classify is still usable with a hand-written column map.
FORMATS = ("chatml", "sharegpt", "alpaca", "preference", "raw_text", "unknown")

#: What each task can actually train on, in preference order. The whole point of
#: the module: this table is why picking DPO with an Alpaca dataset is a question
#: the form can answer instead of a run that quietly optimises nothing.
TASK_FORMATS: dict[str, tuple[str, ...]] = {
    "sft": ("chatml", "sharegpt", "alpaca", "raw_text"),
    "cpt": ("raw_text",),
    "dpo": ("preference",),
    "kto": ("preference",),
    "reward": ("preference",),
    "grpo": ("chatml", "sharegpt", "alpaca"),
    "pretrain": ("raw_text",),
}

#: Column names each shape is usually spelled with. Order matters — the first
#: present one wins, so `messages` beats `conversations` for ChatML.
_CHAT_COLUMNS = ("messages", "conversation", "conversations", "chat")
_TEXT_COLUMNS = ("text", "content", "document", "raw", "completion")
_PROMPT_COLUMNS = ("prompt", "question", "instruction", "query", "input")
_CHOSEN_COLUMNS = ("chosen", "response_a", "preferred", "chosen_response")
_REJECTED_COLUMNS = ("rejected", "response_b", "dispreferred", "rejected_response")


@dataclass
class Detection:
    """One shape's verdict on a sample of rows."""

    format: str
    #: 0..1. Above 0.5 the pane states it; below, it offers it as a guess.
    confidence: float
    #: Human sentence naming the evidence. Rendered verbatim — it is the whole
    #: reason this is a verdict rather than a coercion.
    reason: str
    columns: dict[str, str] = field(default_factory=dict)

    @property
    def certain(self) -> bool:
        return self.confidence >= 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "columns": dict(self.columns),
            "certain": self.certain,
        }


@dataclass
class Adaptation:
    """Whether a detected shape can feed a task, and how."""

    ok: bool
    #: The `column_map` a recipe carries: role -> column name.
    columns: dict[str, str] = field(default_factory=dict)
    #: Why not, when `ok` is False. Never empty in that case.
    problem: str = ""
    #: True when the data must be reshaped in the generated code rather than just
    #: renamed — the recipe emits an explicit `formatting_func` for these.
    needs_formatting: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "columns": dict(self.columns),
            "problem": self.problem,
            "needsFormatting": self.needs_formatting,
        }


def _first_present(columns: list[str], candidates: tuple[str, ...]) -> str:
    lowered = {c.lower(): c for c in columns}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return ""


def _messages_dialect(rows: list[dict[str, Any]], column: str) -> tuple[str, int]:
    """Which chat dialect a message-list column speaks, and in how many rows.

    ChatML and ShareGPT differ only in the *keys inside the list items*
    (`role`/`content` vs `from`/`value`), which is exactly the kind of difference a
    column-name check cannot see — and getting it wrong means `apply_chat_template`
    silently produces empty turns.
    """
    chatml = sharegpt = 0
    for row in rows:
        value = row.get(column)
        if not isinstance(value, list) or not value:
            continue
        head = value[0]
        if not isinstance(head, dict):
            continue
        if "role" in head and "content" in head:
            chatml += 1
        elif "from" in head and "value" in head:
            sharegpt += 1
    if chatml >= sharegpt and chatml:
        return "chatml", chatml
    if sharegpt:
        return "sharegpt", sharegpt
    return "", 0


def _looks_textual(rows: list[dict[str, Any]], column: str) -> int:
    """How many rows hold a non-empty *string* in this column.

    A column can be named `text` and hold a list, a dict or a null. Training on the
    repr of any of those is the failure this counts against.
    """
    return sum(
        1 for row in rows if isinstance(row.get(column), str) and row[column].strip()
    )


def detect(columns: list[str], rows: list[dict[str, Any]]) -> Detection:
    """The best verdict on this dataset's shape, with its evidence.

    Ordered most-specific first: a preference set also has a `prompt` column and
    would otherwise be misread as Alpaca, and that particular confusion is the one
    that silently trains DPO data with an SFT objective.
    """
    total = len(rows)
    if not columns:
        return Detection("unknown", 0.0, "no columns were reported")
    if not total:
        return Detection(
            "unknown", 0.0, "columns are known but no sample rows came back"
        )

    # --- preference (must come first: it overlaps every other shape) ---
    chosen = _first_present(columns, _CHOSEN_COLUMNS)
    rejected = _first_present(columns, _REJECTED_COLUMNS)
    if chosen and rejected:
        hits = sum(
            1 for r in rows if r.get(chosen) is not None and r.get(rejected) is not None
        )
        prompt = _first_present(columns, _PROMPT_COLUMNS)
        cols = {"chosen": chosen, "rejected": rejected}
        if prompt:
            cols["prompt"] = prompt
        return Detection(
            "preference",
            hits / total,
            f"`{chosen}` and `{rejected}` are both populated in {hits} of {total} "
            f"rows — a preference set, trainable by DPO/KTO/reward but not by SFT.",
            cols,
        )

    # --- chat (chatml / sharegpt), told apart by the keys inside the list ---
    chat_column = _first_present(columns, _CHAT_COLUMNS)
    if chat_column:
        dialect, hits = _messages_dialect(rows, chat_column)
        if dialect:
            keys = "role/content" if dialect == "chatml" else "from/value"
            return Detection(
                dialect,
                hits / total,
                f"`{chat_column}` holds {keys} pairs in {hits} of {total} rows.",
                {"messages": chat_column},
            )

    # --- alpaca ---
    instruction = _first_present(columns, ("instruction",))
    output = _first_present(columns, ("output", "response", "answer"))
    if instruction and output:
        hits = sum(1 for r in rows if str(r.get(instruction) or "").strip())
        cols = {"instruction": instruction, "output": output}
        extra = _first_present(columns, ("input", "context"))
        if extra:
            cols["input"] = extra
        return Detection(
            "alpaca",
            hits / total,
            f"`{instruction}` and `{output}` are present"
            + (f", with `{extra}` as optional context" if extra else "")
            + f" — instruction data in {hits} of {total} rows.",
            cols,
        )

    # --- raw text ---
    text = _first_present(columns, _TEXT_COLUMNS)
    if text:
        hits = _looks_textual(rows, text)
        if hits:
            return Detection(
                "raw_text",
                hits / total,
                f"`{text}` holds plain strings in {hits} of {total} rows.",
                {"text": text},
            )
        return Detection(
            "unknown",
            0.0,
            f"`{text}` looks like a text column but holds "
            f"{type(rows[0].get(text)).__name__} rather than strings.",
        )

    return Detection(
        "unknown",
        0.0,
        "none of the known shapes matched. Columns are: " + ", ".join(columns),
    )


def adapt(detection: Detection, task: str) -> Adaptation:
    """Can `task` train on this shape, and with what column map?

    The refusal is the feature. `trl` will happily accept a dataset that has the
    wrong objective's columns and train on whatever it finds, so "no" said here
    with a reason is worth more than a mapping invented to keep the form green.
    """
    wanted = TASK_FORMATS.get(task)
    if wanted is None:
        return Adaptation(False, problem=f"unknown task {task!r}")

    if detection.format == "unknown":
        return Adaptation(
            False,
            problem=(
                "the dataset's shape could not be determined, so no column map can "
                "be inferred. Map the columns by hand to continue."
            ),
        )

    if detection.format not in wanted:
        return Adaptation(
            False,
            problem=(
                f"this is {detection.format} data and {task} needs "
                f"{' or '.join(wanted)}. "
                + (
                    "A preference set carries a chosen and a rejected answer; SFT has "
                    "no way to use the rejected one."
                    if detection.format == "preference"
                    else f"Pick a {wanted[0]} dataset, or a task that trains on "
                    f"{detection.format}."
                )
            ),
        )

    # In range. `raw_text` and `preference` map straight through; the chat and
    # instruction shapes need the generated code to build the text, which is why
    # `needs_formatting` exists rather than a silent rename.
    needs = detection.format in ("sharegpt", "alpaca")
    return Adaptation(True, columns=dict(detection.columns), needs_formatting=needs)


def formatting_source(fmt: str, columns: dict[str, str]) -> str:
    """The `formatting_func` body a recipe emits for a shape that needs reshaping.

    Emitted **explicitly into the notebook**, never applied invisibly: the whole
    contract of the recipe surface is that the generated cells are the truth and
    are yours to edit. A hidden transform between the dataset you picked and the
    tokens the model saw would be the one part of the pipeline you could not read.
    """
    if fmt == "sharegpt":
        messages = columns.get("messages", "conversations")
        return (
            "def to_messages(example):\n"
            "    # ShareGPT from/value -> the role/content pairs trl's chat\n"
            "    # template expects.\n"
            "    roles = {'human': 'user', 'gpt': 'assistant', 'system': 'system'}\n"
            f"    turns = example[{messages!r}]\n"
            "    return {'messages': [\n"
            "        {'role': roles.get(t.get('from'), 'user'),\n"
            "         'content': t.get('value', '')}\n"
            "        for t in turns\n"
            "    ]}\n"
            "\n"
            "dataset = dataset.map(to_messages)"
        )
    if fmt == "alpaca":
        instruction = columns.get("instruction", "instruction")
        output = columns.get("output", "output")
        extra = columns.get("input", "")
        prompt = (
            f"example[{instruction!r}]"
            if not extra
            else f"example[{instruction!r}] + "
            f"('\\n\\n' + example[{extra!r}] if example.get({extra!r}) else '')"
        )
        return (
            "def to_messages(example):\n"
            "    # Alpaca instruction/input/output -> chat turns, so the model's own\n"
            "    # template decides the prompt format rather than a hardcoded one.\n"
            "    return {'messages': [\n"
            f"        {{'role': 'user', 'content': {prompt}}},\n"
            f"        {{'role': 'assistant', 'content': example[{output!r}]}},\n"
            "    ]}\n"
            "\n"
            "dataset = dataset.map(to_messages)"
        )
    return ""


def text_field_for(fmt: str, columns: dict[str, str]) -> str:
    """The column trl should read after any `formatting_func` has run.

    `messages` for everything chat-shaped, because both reshapers above emit that
    key; the mapped text column for raw corpora. This replaces the free-text
    `text_field` the form used to ask for.
    """
    if fmt in ("chatml", "sharegpt", "alpaca"):
        return columns.get("messages", "messages") if fmt == "chatml" else "messages"
    if fmt == "raw_text":
        return columns.get("text", "text")
    return ""
