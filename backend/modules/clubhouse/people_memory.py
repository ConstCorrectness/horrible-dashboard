"""Persistent user knowledge and profile memory store for the Clubhouse Voice Agent.

Enables the agent to remember people across Clubhouse rooms, learn facts from their
conversations and bios, and maintain social relationships over time.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from backend import jsonstore, paths

logger = logging.getLogger(__name__)


@dataclass
class PersonMemory:
    """Persistent knowledge about one Clubhouse user."""

    user_id: int
    name: str
    username: str = ""
    bio: str | None = None
    photo_url: str | None = None
    twitter: str | None = None
    instagram: str | None = None
    notes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    rooms_seen: list[str] = field(default_factory=list)
    first_seen_ts: float = field(default_factory=time.time)
    last_seen_ts: float = field(default_factory=time.time)
    interaction_count: int = 0
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonMemory:
        return cls(
            user_id=int(data.get("user_id") or 0),
            name=str(data.get("name") or "").strip(),
            username=str(data.get("username") or "").strip(),
            bio=data.get("bio") or None,
            photo_url=data.get("photo_url") or None,
            twitter=data.get("twitter") or None,
            instagram=data.get("instagram") or None,
            notes=list(data.get("notes") or []),
            tags=list(data.get("tags") or []),
            rooms_seen=list(data.get("rooms_seen") or []),
            first_seen_ts=float(data.get("first_seen_ts") or time.time()),
            last_seen_ts=float(data.get("last_seen_ts") or time.time()),
            interaction_count=int(data.get("interaction_count") or 0),
            summary=data.get("summary") or None,
        )


class PeopleMemoryStore:
    """Manages persistent profiles, learned notes, and social memory for Clubhouse users."""

    def __init__(self, storage_path: Path | None = None) -> None:
        self._path = storage_path or (paths.data_dir() / "clubhouse-people-memory.json")
        self._people: dict[int, PersonMemory] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for item in raw:
                person = PersonMemory.from_dict(item)
                if person.user_id > 0:
                    self._people[person.user_id] = person
        except Exception as e:
            logger.warning(
                "Failed to load clubhouse people memory from %s: %s", self._path, e
            )

    def _save(self) -> None:
        try:
            data = [p.to_dict() for p in self._people.values()]
            # `Path.replace` is `MoveFileEx` on Windows and raises PermissionError
            # while a reader holds the destination open — the hand-rolled temp-file
            # dance here had the right idea and the wrong last line.
            jsonstore.write_text(self._path, json.dumps(data, indent=2))
        except Exception as e:
            logger.error("Failed to save clubhouse people memory: %s", e)

    def get(self, user_id: int) -> PersonMemory | None:
        self._ensure_loaded()
        return self._people.get(user_id)

    def find_by_name_or_username(self, query: str) -> PersonMemory | None:
        self._ensure_loaded()
        q = query.strip().lower().lstrip("@")
        if not q:
            return None
        # 1. Exact username match
        for p in self._people.values():
            if p.username.lower() == q:
                return p
        # 2. Exact full name match
        for p in self._people.values():
            if p.name.lower() == q:
                return p
        # 3. Partial first name / substring
        for p in self._people.values():
            if q in p.name.lower() or (p.username and q in p.username.lower()):
                return p
        return None

    def list_all(self, limit: int = 100) -> list[PersonMemory]:
        self._ensure_loaded()
        # Sort by last seen descending
        return sorted(
            self._people.values(), key=lambda p: p.last_seen_ts, reverse=True
        )[:limit]

    def search(self, query: str) -> list[PersonMemory]:
        self._ensure_loaded()
        q = query.strip().lower().lstrip("@")
        if not q:
            return self.list_all(50)
        results = []
        for p in self._people.values():
            if (
                q in p.name.lower()
                or (p.username and q in p.username.lower())
                or (p.bio and q in p.bio.lower())
                or any(q in note.lower() for note in p.notes)
                or any(q in tag.lower() for tag in p.tags)
            ):
                results.append(p)
        return sorted(results, key=lambda p: p.last_seen_ts, reverse=True)

    def learn_user(
        self,
        user_id: int,
        name: str,
        *,
        username: str = "",
        bio: str | None = None,
        photo_url: str | None = None,
        twitter: str | None = None,
        instagram: str | None = None,
        room_topic: str | None = None,
    ) -> PersonMemory:
        """Register or update a user's known profile and presence."""
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        self._ensure_loaded()
        now = time.time()
        person = self._people.get(user_id)
        if person is None:
            person = PersonMemory(
                user_id=user_id,
                name=name or f"User {user_id}",
                username=username,
                bio=bio,
                photo_url=photo_url,
                twitter=twitter,
                instagram=instagram,
                first_seen_ts=now,
                last_seen_ts=now,
                interaction_count=1,
            )
            self._people[user_id] = person
        else:
            if name:
                person.name = name
            if username:
                person.username = username
            if bio is not None:
                person.bio = bio
            if photo_url:
                person.photo_url = photo_url
            if twitter is not None:
                person.twitter = twitter
            if instagram is not None:
                person.instagram = instagram
            person.last_seen_ts = now
            person.interaction_count += 1

        if room_topic and room_topic not in person.rooms_seen:
            person.rooms_seen.append(room_topic)
            if len(person.rooms_seen) > 10:
                person.rooms_seen = person.rooms_seen[-10:]

        self._save()
        return person

    def add_note(self, user_id_or_name: int | str, note: str) -> PersonMemory | None:
        self._ensure_loaded()
        person = (
            self.get(user_id_or_name)
            if isinstance(user_id_or_name, int)
            else self.find_by_name_or_username(str(user_id_or_name))
        )
        if person is None:
            return None
        cleaned_note = note.strip()
        if cleaned_note and cleaned_note not in person.notes:
            person.notes.append(cleaned_note)
            if len(person.notes) > 20:
                person.notes = person.notes[-20:]
            self._save()
        return person

    def remove_note(
        self, user_id_or_name: int | str, note_idx: int
    ) -> PersonMemory | None:
        self._ensure_loaded()
        person = (
            self.get(user_id_or_name)
            if isinstance(user_id_or_name, int)
            else self.find_by_name_or_username(str(user_id_or_name))
        )
        if person is None:
            return None
        if 0 <= note_idx < len(person.notes):
            person.notes.pop(note_idx)
            self._save()
        return person

    def add_tag(self, user_id_or_name: int | str, tag: str) -> PersonMemory | None:
        self._ensure_loaded()
        person = (
            self.get(user_id_or_name)
            if isinstance(user_id_or_name, int)
            else self.find_by_name_or_username(str(user_id_or_name))
        )
        if person is None:
            return None
        t = tag.strip().lower()
        if t and t not in person.tags:
            person.tags.append(t)
            self._save()
        return person

    def remove_tag(self, user_id_or_name: int | str, tag: str) -> PersonMemory | None:
        self._ensure_loaded()
        person = (
            self.get(user_id_or_name)
            if isinstance(user_id_or_name, int)
            else self.find_by_name_or_username(str(user_id_or_name))
        )
        if person is None:
            return None
        t = tag.strip().lower()
        if t in person.tags:
            person.tags.remove(t)
            self._save()
        return person

    def forget_person(self, user_id_or_name: int | str) -> bool:
        self._ensure_loaded()
        person = (
            self.get(user_id_or_name)
            if isinstance(user_id_or_name, int)
            else self.find_by_name_or_username(str(user_id_or_name))
        )
        if person and person.user_id in self._people:
            del self._people[person.user_id]
            self._save()
            return True
        return False

    def auto_extract_facts(self, speaker_name: str, utterance: str) -> list[str]:
        """Heuristically extract self-disclosed personal facts from spoken speech."""
        if not speaker_name or not utterance:
            return []
        person = self.find_by_name_or_username(speaker_name)
        if person is None:
            return []

        patterns = [
            r"\b(?:i am|i'm) (?:a|an) ([a-zA-Z0-9_\- ]{3,40})(?:\.|\,|$)",
            r"\b(?:i work at|i work for) ([a-zA-Z0-9_\- ]{2,30})(?:\.|\,|$)",
            r"\b(?:i live in|i'm based in|i am from) ([a-zA-Z0-9_\- ]{2,30})(?:\.|\,|$)",
            r"\b(?:i'm building|i am building|i'm working on|i am working on) ([a-zA-Z0-9_\- ]{3,50})(?:\.|\,|$)",
            r"\b(?:my project is|my company is) ([a-zA-Z0-9_\- ]{2,40})(?:\.|\,|$)",
        ]
        extracted: list[str] = []
        lowered = utterance.strip()
        for pat in patterns:
            match = re.search(pat, lowered, re.I)
            if match:
                fact_detail = match.group(0).strip("., ")
                if len(fact_detail) > 4 and fact_detail not in person.notes:
                    person.notes.append(fact_detail)
                    extracted.append(fact_detail)

        if extracted:
            self._save()
        return extracted

    def format_room_memory(self, user_ids: list[int]) -> str | None:
        """Format a rich memory briefing about people currently present for the agent prompt."""
        self._ensure_loaded()
        lines: list[str] = []
        for uid in user_ids:
            p = self._people.get(uid)
            if not p:
                continue
            entry = f"- {p.name}"
            if p.username:
                entry += f" (@{p.username})"
            parts: list[str] = []
            if p.bio:
                clean_bio = " ".join(p.bio.split())[:120]
                parts.append(clean_bio)
            if p.notes:
                parts.append("Learned: " + "; ".join(p.notes[-3:]))
            if p.tags:
                parts.append("Tags: " + ", ".join(p.tags))
            if parts:
                entry += ": " + " | ".join(parts)
            lines.append(entry)

        if not lines:
            return None
        return "What you remember about people in this room:\n" + "\n".join(lines[:12])


# Process-global store instance
people_memory_store = PeopleMemoryStore()
