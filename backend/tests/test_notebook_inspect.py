"""Kernel introspection (`inspect_request`) behind the docs popup.

The hazard here is not the protocol, it is the *threading*. All zmq traffic belongs
to the session's worker thread, and shell replies are read by whichever loop
happens to be pumping — so a reply has to be routed by its parent msg_id rather
than assumed to belong to the read that found it. Before that routing existed, an
inspect reply arriving while a cell was running was swallowed by the execute path's
`_await_reply`, and the lookup sat until its timeout.

These drive the routing logic directly with a fake client; spawning a real kernel
belongs in the lifecycle tests.
"""

from __future__ import annotations

import queue
import threading

from backend.notebook_core.session import _Inspect


class FakeSession:
    """The routing half of `KernelSession`, with the real methods bound to it.

    Binding the real functions (rather than reimplementing them) is the point: a
    stub that reimplements `_route_shell` tests the stub.
    """

    def __init__(self) -> None:
        from backend.notebook_core import session as S

        self.inspect_q: queue.Queue = queue.Queue()
        self.inspect_pending: dict[str, _Inspect] = {}
        self.closing = False
        self.key = "nb:test"
        self.sent: list[tuple[str, int, int]] = []
        self._service_inspects = S.KernelSession._service_inspects.__get__(self)
        self._route_shell = S.KernelSession._route_shell.__get__(self)
        self._fail_pending_inspects = S.KernelSession._fail_pending_inspects.__get__(
            self
        )

        outer = self

        class KC:
            def inspect(self, code: str, cursor_pos: int, detail_level: int) -> str:
                outer.sent.append((code, cursor_pos, detail_level))
                return f"msg-{len(outer.sent)}"

        self.kc = KC()


def test_service_inspects_sends_and_records_the_pending_lookup() -> None:
    s = FakeSession()
    item = _Inspect("json.dumps", 10, 0)
    s.inspect_q.put(item)
    s._service_inspects()
    assert s.sent == [("json.dumps", 10, 0)]
    assert list(s.inspect_pending) == ["msg-1"]
    # Nothing delivered yet — the reply has not arrived.
    assert item.result.empty()


def test_route_shell_delivers_a_reply_to_the_lookup_that_asked() -> None:
    s = FakeSession()
    item = _Inspect("x", 1, 0)
    s.inspect_q.put(item)
    s._service_inspects()

    consumed = s._route_shell(
        {
            "parent_header": {"msg_id": "msg-1"},
            "content": {"status": "ok", "found": True},
        }
    )
    assert consumed is True
    assert item.result.get_nowait() == {"status": "ok", "found": True}
    # Popped, so a duplicate reply can't deliver twice into a maxsize-1 queue.
    assert s.inspect_pending == {}


def test_route_shell_ignores_an_execute_reply() -> None:
    s = FakeSession()
    # Returning False is what lets `_await_reply` keep its own reply; a router that
    # claimed everything would eat the execute reply and hang the cell forever.
    assert (
        s._route_shell({"parent_header": {"msg_id": "some-execute"}, "content": {}})
        is False
    )


def test_two_lookups_in_flight_are_routed_independently() -> None:
    s = FakeSession()
    first, second = _Inspect("a", 1, 0), _Inspect("b", 1, 0)
    s.inspect_q.put(first)
    s.inspect_q.put(second)
    s._service_inspects()
    assert len(s.inspect_pending) == 2

    # Out of order on purpose: the kernel is not obliged to answer in order.
    s._route_shell({"parent_header": {"msg_id": "msg-2"}, "content": {"which": "b"}})
    assert second.result.get_nowait() == {"which": "b"}
    assert first.result.empty()
    s._route_shell({"parent_header": {"msg_id": "msg-1"}, "content": {"which": "a"}})
    assert first.result.get_nowait() == {"which": "a"}


def test_a_dead_kernel_releases_every_waiter() -> None:
    s = FakeSession()
    item = _Inspect("x", 1, 0)
    s.inspect_q.put(item)
    s._service_inspects()
    s._fail_pending_inspects()
    # Released with an error rather than left to each caller's own timeout — one
    # dead lookup per keypress otherwise.
    assert item.result.get_nowait() == {"status": "error"}
    assert s.inspect_pending == {}


def test_a_send_failure_releases_its_waiter() -> None:
    s = FakeSession()

    class Boom:
        def inspect(self, *a, **k):
            raise RuntimeError("socket closed")

    s.kc = Boom()
    item = _Inspect("x", 1, 0)
    s.inspect_q.put(item)
    s._service_inspects()
    # The worker survives (no exception escapes) and the caller is not left hanging.
    assert item.result.get_nowait() == {"status": "error"}


def test_the_waiter_blocks_until_the_worker_answers() -> None:
    """The caller's side: `queue.Queue.get`, which is what `to_thread` parks on.

    This is why waiting for a lookup on a plain executor thread is safe even though
    a kernel call on one is not — the waiter touches no zmq socket at all.
    """
    s = FakeSession()
    item = _Inspect("x", 1, 0)
    s.inspect_q.put(item)
    s._service_inspects()

    got: list[dict] = []

    def wait() -> None:
        got.append(item.result.get(timeout=2))

    t = threading.Thread(target=wait)
    t.start()
    s._route_shell({"parent_header": {"msg_id": "msg-1"}, "content": {"status": "ok"}})
    t.join(timeout=2)
    assert got == [{"status": "ok"}]
