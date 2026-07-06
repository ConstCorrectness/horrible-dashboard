"""The node's built-in duel solver — the **skill floor** for agentic-task duels.

When a `your_turn` carries an *open action* (a `payload`-declaring legal action,
e.g. a RAG race's `submit`), auto-play can't just pick an id — it must produce
content. This module is the deliberately-naive baseline that keeps every table
moving: pure keyword-overlap sentence extraction, no model, no vector store.

It exists so a fresh node always finishes a race with *some* score; beating it is
the point of the game. A real harness answers through the manual path
(`game.chooseAction` with a payload) with whatever retrieval stack its author
engineered — library-module ingestion, query decomposition, span extraction.
"""

from __future__ import annotations

import re
from typing import Any

# Words too common to signal relevance in a question.
_STOPWORDS = frozenset(
    "the a an of in on at is was are were be to for with by from what who which "
    "whose when where how why did does do name many much its it's it their there "
    "and or not no".split()
)


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in re.sub(r"[^a-z0-9]+", " ", text.casefold()).split()
        if t and t not in _STOPWORDS
    }


def _sentences(docs: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for doc in docs:
        text = str(doc.get("text") or "")
        out.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip())
    return out


def solve_answers(observation: dict[str, Any]) -> dict[str, str]:
    """Answer every question with the corpus sentence sharing the most keywords.

    Returns a `{question_id: answer}` payload trimmed to the duel's answer cap.
    Empty corpus or questions grade as empty answers — the race still completes.
    """
    docs = list(observation.get("docs") or [])
    questions = list(observation.get("questions") or [])
    max_chars = int(observation.get("max_answer_chars") or 200)
    sentences = _sentences(docs)
    answers: dict[str, str] = {}
    for q in questions:
        prompt_tokens = _tokens(str(q.get("prompt") or ""))
        best, best_score = "", 0
        for sentence in sentences:
            score = len(prompt_tokens & _tokens(sentence))
            if score > best_score:
                best, best_score = sentence, score
        if best:
            answers[str(q.get("id"))] = best[:max_chars]
    return answers


def find_open_action(legal_actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first legal action that declares a payload kind, or None. Open actions
    mark turns the solver (not the pick-an-id policy) must handle."""
    for action in legal_actions:
        if (action.get("params") or {}).get("payload"):
            return action
    return None
