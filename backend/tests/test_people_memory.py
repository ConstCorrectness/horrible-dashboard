from pathlib import Path
from backend.modules.clubhouse.people_memory import PeopleMemoryStore


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


# --- the store is process-global, and that used to leak ------------------------


def test_reset_clears_memory_without_touching_disk(tmp_path: Path):
    """`reset()` is what stops one test's people being visible to the next.

    This object is a module-level singleton constructed at import, so its in-memory
    dict outlives every test in a run. `conftest.reset_process_global_stores` calls
    this; without it, a room member learned in one test reappears in another.
    """
    store_file = tmp_path / "people.json"
    store = PeopleMemoryStore(storage_path=store_file)
    store.learn_user(user_id=1, name="Ada")
    assert store.get(1) is not None

    store.reset()
    # Forgotten in memory...
    assert store._people == {}
    # ...but the file is untouched, so a real (non-test) reset re-reads it.
    assert store_file.is_file()
    assert store.get(1) is not None


def test_the_path_follows_the_data_dir_rather_than_freezing_at_import(
    tmp_path: Path, monkeypatch
):
    """The reason `conftest`'s `HORRIBLE_DATA_DIR` isolation could not reach it.

    `paths` reads the environment, and the singleton is built when the module is
    first imported — so a path resolved in `__init__` is pinned to whatever the
    data dir was at import time, and every later test writes and reads somewhere
    other than its own tmp dir.
    """
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(first))
    store = PeopleMemoryStore()  # no explicit path: follows the data dir
    store.learn_user(user_id=7, name="Ada")
    assert (first / "clubhouse-people-memory.json").is_file()
    assert store.get(7) is not None

    # Point at a different data dir: the store must answer from *there*, not serve
    # the previous directory's contents out of its cache.
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(second))
    assert store.get(7) is None
    assert not (second / "clubhouse-people-memory.json").is_file()

    # ...and going back finds the original again.
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(first))
    assert store.get(7) is not None


def test_an_explicit_path_still_wins_over_the_data_dir(tmp_path: Path, monkeypatch):
    """`test_people_memory_store_crud` and every other caller that passes a path
    must keep working regardless of what the environment says."""
    explicit = tmp_path / "explicit.json"
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "elsewhere"))
    store = PeopleMemoryStore(storage_path=explicit)
    store.learn_user(user_id=3, name="Grace")
    assert explicit.is_file()
    assert not (tmp_path / "elsewhere").exists()
