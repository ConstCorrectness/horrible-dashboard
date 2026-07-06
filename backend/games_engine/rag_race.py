"""RAG race: the first **agentic-task duel** — retrieval engineering as a game.

Both seats get the *same* mini-corpus and question set in one simultaneous turn
(`current_players()` returns every unsubmitted seat) and race the move clock to
submit answers as one **open action**: the choice is still an enumerated legal
action (`submit`), but its `payload` is free-form data — a `{question_id: answer}`
dict the engine validates and grades server-side.

The skill curve lives in *how* a node answers: the baseline solver does keyword
overlap; a good harness ingests the docs into the vector store, decomposes
queries, and extracts precise spans. Answers are capped at `ANSWER_MAX_CHARS`
so submitting the whole corpus can't farm containment credit — precision is the
skill being graded.

Anti-cheat matches the challenge track: acceptable answers never go over the wire
(observations carry docs + prompts only; `public_state` reveals the key after the
race, for learning). The bundled set uses **fictional** facts so a model can't
answer from pretraining — retrieval is forced. Like the challenge track, the
bundled set is honor-system for source-readers; a hosted server can swap in a
private dataset via the factory's `dataset=` kwarg.
"""

from __future__ import annotations

import re
from typing import Any

from backend.games_engine.base import (
    TERMINAL,
    Action,
    GameSpec,
    GameState,
    register_game,
)

# Precision cap: long enough for a sentence-sized answer, far too short to paste
# the corpus in and collect containment credit on everything.
ANSWER_MAX_CHARS = 200

# One simultaneous turn = the whole race; give it minutes, not the board-game clock.
MOVE_TIMEOUT_S = 180.0

# The bundled dataset: a fictional asteroid-mining company, so every answer must be
# *retrieved* from the docs, never recalled from pretraining. Distractor docs share
# vocabulary with the questions to punish sloppy retrieval.
DEFAULT_DATASET: dict[str, Any] = {
    "docs": [
        {
            "id": "history",
            "title": "Company history",
            "text": (
                "Auroralith Industries was founded in 2041 by Ingrid Halversen in "
                "the orbital free port of Port Meridian. The company began as a "
                "three-person salvage outfit before pivoting to asteroid extraction."
            ),
        },
        {
            "id": "flagship",
            "title": "The flagship",
            "text": (
                "The flagship extraction vessel of Auroralith Industries, the "
                "Cinder Lark, completed its maiden voyage to asteroid 2039-KX in "
                "March 2044. The Cinder Lark carries a crew of forty-one."
            ),
        },
        {
            "id": "ore",
            "title": "Primary export",
            "text": (
                "Auroralith's primary export is veyrite, a lattice ore refined "
                "into high-impulse propellant. Annual veyrite yield reached "
                "18,400 tonnes in 2047."
            ),
        },
        {
            "id": "personnel",
            "title": "Personnel",
            "text": (
                "Chief technology officer Dr. Samuel Okoye leads the propulsion "
                "lab at Auroralith Industries. The company employs a staff of 612 "
                "across its stations."
            ),
        },
        {
            "id": "incident",
            "title": "The refinery fire",
            "text": (
                "The Copper Meadow refinery fire of June 2046 halted Auroralith's "
                "production for eleven weeks. An inquiry attributed the fire to a "
                "faulty coolant manifold."
            ),
        },
        {
            "id": "contract",
            "title": "Contracts",
            "text": (
                "Auroralith's largest contract is with the Luna Gateway "
                "Consortium, valued at 2.3 billion credits over ten years for "
                "refined propellant deliveries."
            ),
        },
        # Distractors: adjacent vocabulary, wrong entities.
        {
            "id": "competitor",
            "title": "Competitor profile",
            "text": (
                "Kestrel Deep Mining Cooperative, founded in 2039 by Marta Voss, "
                "operates older extraction vessels out of the Ceres Bazaar and "
                "exports mostly water ice."
            ),
        },
        {
            "id": "schedule",
            "title": "Shipping schedule",
            "text": (
                "Quarterly propellant deliveries depart Port Meridian on the "
                "first Monday of each cycle. Kestrel shipments run a week later "
                "and carry no veyrite."
            ),
        },
    ],
    "questions": [
        {
            "id": "q-founder",
            "prompt": "Who founded Auroralith Industries?",
            "accept": ["ingrid halversen", "halversen"],
        },
        {
            "id": "q-year",
            "prompt": "In what year was Auroralith Industries founded?",
            "accept": ["2041"],
        },
        {
            "id": "q-flagship",
            "prompt": "What is the name of Auroralith's flagship extraction vessel?",
            "accept": ["cinder lark"],
        },
        {
            "id": "q-ore",
            "prompt": "What ore is Auroralith's primary export?",
            "accept": ["veyrite"],
        },
        {
            "id": "q-fire",
            "prompt": (
                "For how many weeks did the Copper Meadow refinery fire halt "
                "production?"
            ),
            "accept": ["eleven", "11"],
        },
        {
            "id": "q-contract",
            "prompt": "Which consortium holds Auroralith's largest contract?",
            "accept": ["luna gateway"],
        },
    ],
}


def _norm(text: str) -> str:
    """Normalize for grading: casefold, strip punctuation, collapse whitespace."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def _matches(answer: str, accept: list[str]) -> bool:
    """Token-boundary containment: an acceptable phrase appearing in the answer
    counts ('founded in 2041' matches '2041'), but substrings inside other tokens
    don't ('2041' never matches inside '20415')."""
    padded = f" {_norm(answer)} "
    return any(f" {_norm(a)} " in padded for a in accept)


class RagRace(GameState):
    def __init__(self, dataset: dict[str, Any] | None = None) -> None:
        ds = dataset or DEFAULT_DATASET
        self.docs: list[dict[str, Any]] = list(ds["docs"])
        self.questions: list[dict[str, Any]] = list(ds["questions"])
        # Per-seat submitted answers ({question_id: answer}); None = still racing.
        self.answers: list[dict[str, str] | None] = [None, None]

    # ---- turn structure ----------------------------------------------------

    def current_players(self) -> list[int]:
        return [s for s in (0, 1) if self.answers[s] is None]

    def current_player(self) -> int:
        pending = self.current_players()
        return TERMINAL if not pending else pending[0]

    def legal_actions(self, player: int) -> list[Action]:
        if player not in self.current_players():
            return []
        return [
            Action(
                id="submit",
                label=f"submit your answers ({len(self.questions)} questions)",
                # `payload: "answers"` marks this an open action: the node sends a
                # {question_id: answer} dict alongside the id.
                params={"payload": "answers", "max_answer_chars": ANSWER_MAX_CHARS},
            )
        ]

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        if player not in self.current_players():
            raise ValueError("this seat has already submitted")
        if action_id != "submit":
            raise ValueError(f"bad action id {action_id!r}")
        self.answers[player] = self._clean(payload)

    def _clean(self, payload: Any) -> dict[str, str]:
        """Validate the submitted payload: keep only known question ids, coerce to
        strings, enforce the length cap. Anything malformed grades as empty."""
        if not isinstance(payload, dict):
            return {}
        known = {str(q["id"]) for q in self.questions}
        return {
            str(k): str(v)[:ANSWER_MAX_CHARS]
            for k, v in payload.items()
            if str(k) in known
        }

    # ---- views -------------------------------------------------------------

    def observation(self, player: int) -> dict[str, Any]:
        # Both seats see the same problem; answers/keys are never included.
        return {
            "game": "rag_race",
            "seat": player,
            "docs": self.docs,
            "questions": [
                {"id": q["id"], "prompt": q["prompt"]} for q in self.questions
            ],
            "max_answer_chars": ANSWER_MAX_CHARS,
            "submitted": [a is not None for a in self.answers],
        }

    def public_state(self) -> dict[str, Any]:
        done = self.is_terminal()
        state: dict[str, Any] = {
            "game": "rag_race",
            "question_count": len(self.questions),
            "questions": [
                {"id": q["id"], "prompt": q["prompt"]} for q in self.questions
            ],
            "submitted": [a is not None for a in self.answers],
            "turn": None,
            "winner": self._winner() if done else None,
        }
        if done:
            # Full reveal for the post-race report card: what each seat answered,
            # what counted, and the acceptable answers (learning > secrecy once
            # the race is over — a fresh table gets a fresh... same bundled set in
            # v1; hosted servers rotate private datasets).
            state["scores"] = [self._score(0), self._score(1)]
            state["results"] = [
                {
                    "id": q["id"],
                    "prompt": q["prompt"],
                    "accept": list(q["accept"]),
                    "answers": [
                        (self.answers[s] or {}).get(str(q["id"]), "") for s in (0, 1)
                    ],
                    "correct": [self._correct(s, q) for s in (0, 1)],
                }
                for q in self.questions
            ]
        return state

    # ---- outcome -----------------------------------------------------------

    def _correct(self, seat: int, question: dict[str, Any]) -> bool:
        answer = (self.answers[seat] or {}).get(str(question["id"]), "")
        return bool(answer) and _matches(answer, list(question["accept"]))

    def _score(self, seat: int) -> int:
        return sum(1 for q in self.questions if self._correct(seat, q))

    def _winner(self) -> int | None:
        s0, s1 = self._score(0), self._score(1)
        if s0 == s1:
            return None
        return 0 if s0 > s1 else 1

    def returns(self) -> dict[int, float]:
        # Zero-sum score delta: sign gives the ladder its W/D/L, magnitude shows
        # how decisive the race was.
        s0, s1 = self._score(0), self._score(1)
        return {0: float(s0 - s1), 1: float(s1 - s0)}


SPEC = register_game(
    GameSpec(
        id="rag_race",
        name="RAG Race",
        min_players=2,
        max_players=2,
        factory=RagRace,
        move_timeout_s=MOVE_TIMEOUT_S,
    )
)
