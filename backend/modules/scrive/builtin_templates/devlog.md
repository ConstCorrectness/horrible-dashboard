---
template:
  name: Devlog
  description: What changed in a project this week, what was learned, and what is next.
  kind: post
  inputs:
    project:
      description: The project this entry is about
      required: true
    period:
      description: The span this entry covers
      default: this week
tags: [devlog]
---

:::{pending}
Lead (no heading): the one change from {{period}} on {{project}} worth reading about, in two sentences.
:::

## What shipped

:::{pending}
The changes as a short list, most interesting first, each with a link (commit, PR, demo) and a screenshot or clip for anything visual.
:::

## What I learned

:::{pending}
One or two lessons, told as a short story: the problem, the wrong first attempt, what worked. Code snippets where they make it concrete.
:::

## What is next

:::{pending}
The next two or three things, honestly scoped.
:::
