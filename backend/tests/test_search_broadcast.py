import asyncio
import pytest

from backend.modules.search.broadcast import (
    crawl_events,
    publish_seed,
    publish_progress,
    publish_page,
    _last_progress
)

@pytest.fixture(autouse=True)
def reset_broadcaster():
    """Reset the singleton state before each test."""
    crawl_events._subscribers.clear()
    _last_progress.clear()
    yield

@pytest.mark.anyio
async def test_publish_seed():
    queue = crawl_events.subscribe()

    seed = {"id": "123", "query": "test"}
    publish_seed(seed)

    event = queue.get_nowait()
    assert event == {"event": "seed", "data": seed}

    crawl_events.unsubscribe(queue)

@pytest.mark.anyio
async def test_publish_progress():
    queue = crawl_events.subscribe()

    stats = {"seed_id": "seed1", "count": 1}
    publish_progress(stats)

    event = queue.get_nowait()
    assert event == {"event": "progress", "data": stats}

    # Second call right away should be throttled
    publish_progress({"seed_id": "seed1", "count": 2})
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()

    # Calling with force=True should bypass throttle
    publish_progress({"seed_id": "seed1", "count": 3}, force=True)
    event = queue.get_nowait()
    assert event == {"event": "progress", "data": {"seed_id": "seed1", "count": 3}}

    crawl_events.unsubscribe(queue)

@pytest.mark.anyio
async def test_publish_page():
    queue = crawl_events.subscribe()

    publish_page("seed123", "https://example.com", "success")

    event = queue.get_nowait()
    assert event == {"event": "page", "data": {"seed_id": "seed123", "url": "https://example.com", "status": "success"}}

    crawl_events.unsubscribe(queue)
