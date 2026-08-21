# horrible-trajectories

Record what your agent **did** — its tool calls, their results, and how the run
turned out — into a [horrible-dashboard](https://github.com/horriblecpp/horrible-dashboard)
node, where it becomes queryable, searchable and exportable as training data.

```bash
pip install horrible-trajectories
```

```python
from horrible_trajectories import TrajectoryRecorder

rec = TrajectoryRecorder(dataset="my-coding-agent")          # HORRIBLE_URL, or base_url=
harness = rec.harness(system_prompt=PROMPT, tools=TOOLS, model="claude-opus-5")

with rec.run(goal="fix the failing test", harness=harness) as run:
    run.message("assistant", "I'll run the tests first.")
    run.action("bash", {"cmd": "pytest"}, {"rc": 1}, ok=False, ms=1200)
    run.label("outcome", "success")
```

## Three properties, on purpose

**It never raises into your agent.** Every network failure is logged at `debug`
and dropped. If the dashboard is down, your agent does not notice — a telemetry
client that can crash the program it measures is worse than no telemetry.

**It never blocks your loop.** Steps go onto a queue drained by a background
thread. `Run.__exit__` flushes synchronously, so a short-lived script does not
exit with data still queued.

**It is idempotent.** Each run carries an `external_id`, so a retry replaces the
run rather than filing a second copy of it.

An exception escaping the `with` block seals the run as `failed` and keeps the
steps recorded up to that point — a crashed run is the most interesting kind.

## A step is one decision

`run.action(name, args, result)` records a tool call **and its result** as one
step. That is not a convenience: pairing calls to results afterwards means
matching by name and ordinal, which is correct until a turn calls the same tool
twice and then is silently wrong.

## The harness is what makes runs comparable

`rec.harness(...)` describes the configuration a run executed under — prompt,
tools, model, sampling settings. The node content-addresses it into a fingerprint,
which is what lets you ask *did my prompt change help* and get an answer grouped
by something real. The fingerprint is computed server-side; two clients hashing
slightly differently would split one harness in two and make every comparison
across them empty.

## API

| Call | Purpose |
| --- | --- |
| `TrajectoryRecorder(dataset, base_url=…, batch_size=…, flush_interval_sec=…)` | Client handle |
| `TrajectoryRecorder.harness(system_prompt=, tools=, model=, provider=, agent_id=, params=, label=)` | Describe a configuration; `tools` takes a list or a `{name: schema}` map |
| `rec.run(goal, harness=, external_id=, model=, meta=)` | Open a run (a context manager) |
| `run.message(role, content)` · `run.thought(content)` | Conversation and reasoning |
| `run.action(name, args, result, ok=, ms=, error=)` | A tool call and its result |
| `run.observation(value)` · `run.reward(value)` | Environment-shaped steps |
| `run.label(key, value, score=, source=, rationale=)` | Attach a judgment |
| `run.finish(status=, outcome=, error=)` | Seal early (the context manager does this) |
| `rec.flush(timeout=)` · `rec.close()` | Drain the queue |

License: MIT.
