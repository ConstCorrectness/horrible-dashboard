"""One relay connection per data directory.

Every backend that shares a data directory is the same node, and the relay broker
keeps one socket per node id: a second registration closes the first. Two backends
up at once therefore knocked each other off the relay every few seconds. The relay
transport is now taken under a bus claim, so only the first process registers.
"""

from __future__ import annotations

import json
import os

import psutil

from backend.kernel import bus
from backend.modules.network import setup
from backend.modules.network.transport.relay import RelayTransport


def _relays(transports):
    return [t for t in transports if isinstance(t, RelayTransport)]


def test_first_process_takes_the_relay(monkeypatch):
    monkeypatch.setenv("HORRIBLE_RELAY_URL", "ws://relay.invalid/relay-ws")
    try:
        assert len(_relays(setup.build_transports())) == 1
        # Building again in the same process is not a second holder.
        assert len(_relays(setup.build_transports())) == 1
    finally:
        setup._release_relay()


def test_second_process_on_the_same_data_dir_does_not_register(monkeypatch):
    monkeypatch.setenv("HORRIBLE_RELAY_URL", "ws://relay.invalid/relay-ws")
    # A live process other than this one holds the claim: the test runner's parent.
    holder = psutil.Process(os.getppid())
    path = bus._claim_path(setup.RELAY_CLAIM)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pid": holder.pid, "startedAt": holder.create_time()}),
        encoding="utf-8",
    )
    try:
        assert _relays(setup.build_transports()) == []
    finally:
        path.unlink(missing_ok=True)


def test_a_dead_holder_does_not_keep_the_relay(monkeypatch):
    monkeypatch.setenv("HORRIBLE_RELAY_URL", "ws://relay.invalid/relay-ws")
    path = bus._claim_path(setup.RELAY_CLAIM)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A pid with a create time that cannot match: the holder crashed long ago.
    path.write_text(
        json.dumps({"pid": os.getppid(), "startedAt": 1.0}), encoding="utf-8"
    )
    try:
        assert len(_relays(setup.build_transports())) == 1
    finally:
        setup._release_relay()


def test_relay_off_takes_no_claim(monkeypatch):
    monkeypatch.setenv("HORRIBLE_RELAY_URL", "off")
    setup.build_transports()
    assert not bus._claim_path(setup.RELAY_CLAIM).exists()
