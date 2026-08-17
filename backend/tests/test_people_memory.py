import pytest
from pathlib import Path
from backend.modules.clubhouse.people_memory import PeopleMemoryStore, PersonMemory


def test_people_memory_store_crud(tmp_path: Path):
    store_file = tmp_path / "people-test.json"
    store = PeopleMemoryStore(storage_path=store_file)

    # 1. Learn user
    p = store.learn_user(
        user_id=123,
        name="Horrible Dev",
        username="horrible",
        bio="Building horrible-dashboard and native FPS",
        room_topic="AI & Audio",
    )
    assert p.user_id == 123
    assert p.name == "Horrible Dev"
    assert p.username == "horrible"
    assert "AI & Audio" in p.rooms_seen

    # 2. Add notes
    store.add_note(123, "Loves low-latency audio models")
    store.add_note("horrible", "Creator of Horrible Assault")
    assert len(store.get(123).notes) == 2

    # 3. Tags
    store.add_tag(123, "founder")
    assert "founder" in store.get(123).tags

    # 4. Search
    results = store.search("Horrible")
    assert len(results) == 1
    assert results[0].user_id == 123

    # 5. Format prompt memory
    brief = store.format_room_memory([123])
    assert brief is not None
    assert "Horrible Dev (@horrible)" in brief
    assert "Creator of Horrible Assault" in brief

    # 6. Auto-extract facts
    extracted = store.auto_extract_facts("Horrible Dev", "I am building a high-speed game engine in Rust.")
    assert len(extracted) > 0
    assert any("building a high-speed game engine" in note for note in store.get(123).notes)

    # 7. Forget
    assert store.forget_person(123) is True
    assert store.get(123) is None
