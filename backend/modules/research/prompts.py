"""Prompts for the deep-research pipeline.

The design encodes the published lessons from orchestrator-worker research
systems: the lead plans with **explicit effort scaling** (so a simple question
doesn't spawn an armada), every subagent gets a **detailed task spec**
(objective / output format / tool guidance / boundaries — vague specs make
subagents run identical searches), searching goes **breadth-first then
narrows**, and citation verification is a **separate pass** so the synthesis
model's reasoning doesn't contaminate attribution.
"""

from __future__ import annotations

EFFORT_RUBRIC = """Effort scaling (pick the smallest that answers well):
- quick: 1 subagent, 3-10 tool calls total. Simple fact-finding or a single
  concept summary.
- standard: 2-3 subagents, up to 15 tool calls each. A topic with a few facets
  worth splitting (e.g. background + state of the art + open problems).
- deep: up to {max_subagents} subagents, up to 25 tool calls each. Broad or
  contested topics needing coverage from independent angles.
"""

PLAN_PROMPT = """You are the lead researcher planning a research run.

Query: {query}

Requested effort: {effort} ({effort_note})

{rubric}

Decompose the query into subagent tasks. Each subagent works independently and
in parallel with only your task spec to go on, so write specs that cannot
collide or leave gaps: give each a DISTINCT objective, say what its output must
look like, which tools to prefer, and what is out of its scope.

Available subagent tools: arxiv_search/arxiv_get (academic papers),
web_search (general web), fetch_page (read any URL as text),
library_search (the user's own saved knowledge), save_source (archive a
page worth keeping as evidence).

Guidance to bake into tool_guidance: start with broad, short queries to map the
landscape, then narrow; prefer primary sources; note the source of every claim.

Respond with ONLY a JSON object, no prose, in exactly this shape:
{{
  "complexity": "quick" | "standard" | "deep",
  "subagents": [
    {{
      "name": "short-slug",
      "objective": "what this subagent must find out (distinct from the others)",
      "output_format": "what its findings should look like",
      "tool_guidance": "which tools to lead with and how",
      "boundaries": "what it must NOT spend calls on",
      "max_tool_calls": 10
    }}
  ]
}}"""

PLAN_REPAIR_PROMPT = """Your previous reply was not valid JSON of the required
shape ({error}). Reply again with ONLY the JSON object, nothing else."""

SUBAGENT_PROMPT = """You are a research subagent. Complete this task using your
tools, then report.

Objective: {objective}
Output format: {output_format}
Tool guidance: {tool_guidance}
Boundaries: {boundaries}

Rules:
- You have at most {max_tool_calls} tool calls; spend them where they change
  your answer. Start broad, then narrow.
- Ground every claim in something a tool returned. Keep track of which source
  said what — your report must map findings to sources.
- Stop calling tools when additional calls would not change your findings.

When you are done, reply (no tool call) with your findings as text, followed by
a line `SOURCES:` and one line per source in the form
`- title | url | note on what it supports`."""

SYNTHESIS_PROMPT = """You are the lead researcher writing the final report.

Query: {query}

Below are your subagents' findings, each with its sources. Sources are numbered
globally — cite them inline as [n] wherever a claim rests on one.

{findings}

Write a well-structured markdown report that answers the query:
- Open with a short answer/summary paragraph.
- Use sections with headers where the material warrants them.
- Cite sources inline as [n]. Every non-obvious claim gets a citation.
- Note disagreements between sources and open questions honestly.
- Do not invent sources or cite numbers not in the list."""

CITATIONS_PROMPT = """You are the citation checker for a research report. You get
the report and the numbered source list it was written against.

Report:
{report}

Sources:
{sources}

Tasks:
1. Verify every [n] marker refers to a source that plausibly supports the claim
   it is attached to; fix wrong numbers, remove markers with no plausible
   source, and mark clearly unsupported claims with [unverified].
2. Append a `## References` section listing every cited source as
   `[n] title — url`.

Reply with ONLY the corrected report markdown."""
