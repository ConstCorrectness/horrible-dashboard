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
web_search (the live web across every configured engine at once; depth='deep'
also reads the top pages), index_search (this node's own crawled index of ML
sites, blogs and API docs — instant and free, but only covers seeded sites),
fetch_page (read any URL as text), library_search (the user's own saved
knowledge), save_source (archive a page worth keeping as evidence).

Guidance to bake into tool_guidance: start with broad, short queries to map the
landscape, then narrow; prefer primary sources; note the source of every claim.
Reach for index_search and library_search before web_search on documentation and
framework questions — they cost nothing and answer instantly. One web_search
already queries several engines in parallel, so re-running it with reworded
queries duplicates work it does internally; change the angle, not the phrasing.

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

CRITIQUE_PROMPT = """You are the lead researcher reviewing what your subagents
found, before writing anything. Your job is to find the GAPS, not to summarize.

Query: {query}

What you asked for:
{plan}

What came back (sources numbered globally):
{findings}
{followups}
Judge honestly:
- Which parts of the query are still unanswered or only thinly supported?
- Where does the evidence rest on a single source, or on sources that would all
  say the same thing?
- What did a subagent claim without showing where it came from?

If the findings genuinely answer the query, say so and stop — spawning another
round to confirm what you already know is waste. Otherwise write up to
{max_subagents} NEW subagent tasks that target the gaps specifically. Do not
re-run work that has already been done.

Respond with ONLY a JSON object, no prose, in exactly this shape:
{{
  "sufficient": true | false,
  "gaps": ["what is still missing, one per entry"],
  "subagents": [
    {{
      "name": "short-slug",
      "objective": "the specific gap this closes",
      "output_format": "what its findings should look like",
      "tool_guidance": "which tools to lead with and how",
      "boundaries": "what it must NOT spend calls on",
      "max_tool_calls": 8
    }}
  ]
}}

When "sufficient" is true, "subagents" must be an empty array."""

CRITIQUE_REPAIR_PROMPT = """Your previous reply was not valid JSON of the required
shape ({error}). Reply again with ONLY the JSON object, nothing else."""

VERIFY_EXTRACT_PROMPT = """You are auditing a research report for how well its
claims are supported.

Report:
{report}

Extract the report's LOAD-BEARING factual claims — the ones a reader would act
on, or would be misled by if they were wrong. Skip background, definitions and
hedged statements. Aim for {max_claims} at most; fewer is fine.

For each, record the citation numbers [n] actually attached to it in the report.
A claim with no marker gets an empty list.

Respond with ONLY a JSON array, no prose:
[{{"claim": "the claim in one sentence", "citations": [1, 4]}}]"""

CONTRADICTIONS_PROMPT = """You are checking a body of research findings for
DISAGREEMENT between sources.

Query: {query}

Findings (sources numbered globally):
{findings}

Find places where sources genuinely conflict — different numbers for the same
quantity, opposite conclusions, claims that cannot both be true. Ignore
differences of emphasis, wording, or scope. If sources broadly agree, return an
empty array; inventing conflict is worse than reporting none.

Respond with ONLY a JSON array, no prose:
[{{"topic": "what they disagree about",
   "positions": [{{"source": 1, "claim": "what this source says"}}]}}]"""

CITATIONS_PROMPT = """You are the citation checker for a research report. You get
the report, the numbered source list it was written against, and an independence
audit of its claims.

Report:
{report}

Sources:
{sources}

Audit:
{verification}

Tasks:
1. Verify every [n] marker refers to a source that plausibly supports the claim
   it is attached to; fix wrong numbers, remove markers with no plausible
   source, and mark clearly unsupported claims with [unverified].
2. For every claim the audit marks `single-sourced`, add "(single source)" after
   its citation in the body. Do not delete it and do not soften the wording —
   the reader decides what one source is worth.
3. Append a `## References` section listing every cited source as
   `[n] title — url`.
4. If the audit lists any contradictions or unsupported claims, append a final
   `## Confidence & caveats` section: one bullet per contradiction naming both
   sides with their [n], and one bullet per unsupported claim. Omit the section
   entirely when the audit is clean — an empty caveats section reads as a
   thoroughness the report hasn't earned.

Reply with ONLY the corrected report markdown."""
