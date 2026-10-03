---
template:
  name: Release notes
  description: What a release changes for the people who use it.
  kind: post
  inputs:
    product:
      description: The product or library being released
      required: true
    version:
      description: The version number
      required: true
tags: [release]
---

:::{pending}
Lead (no heading): {{product}} {{version}} in one sentence — the change users will notice first — and how to upgrade in one line of code.
:::

## Highlights

:::{pending}
The two to four changes that matter most, each a short subsection or bold-led paragraph with a code example or screenshot.
:::

## Breaking changes

:::{pending}
Every change that requires users to act, each with a before/after snippet. Wrap the whole list in a {warning} admonition. If there are none, say so in one line.
:::

## Everything else

:::{pending}
Smaller features and fixes as a flat list, each linking its issue or PR.
:::

## Upgrading

:::{pending}
The upgrade command and any migration steps, in order.
:::
