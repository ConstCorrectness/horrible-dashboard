"""Adapters: other sources of trajectories, normalised to `TrajectoryWrite`.

Each is a pure function taking already-parsed input, so it can be tested against a
fixture with no database and no network. The one place they converge is
`store.ingest_run`, which is also what the HTTP `/ingest` route and the Python SDK
use — one normalisation seam, one place to fix.

There is no `peer.py`. A peer-driven turn runs through `run_agent_turn` on *this*
node (see `network/agent_bridge.handle_remote_agent_request`), so the live recorder
already captures it; all it needed was provenance, which `recorder.begin` reads off
the connection. An adapter would have been a second, divergent capture path for
runs that were never leaving the orchestrator loop in the first place.
"""
