# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

One React frontend serves both layouts. `apps/desktop` is a Tauri shell wrapping
that same web frontend, so its design language is web, not native — never fork a
component per platform; branch on a platform capability check instead.
`apps/mobile-android` (Kotlin + Compose) is a genuinely native companion, but it is
a _peer on the fabric_, not a second dashboard, and is out of scope for this record.

## Users

Primary: a person operating their own node — a single machine that runs local
models, holds their credentials, and talks to other people's nodes. Two tiers of
that person share one product:

- **Everyone, at the agent surface.** The `home` view — 3D avatar, ask bar,
  connector tiles — is the default on open and must work for someone who never
  opens a terminal. Asking for something in plain language is the entry path;
  the agent opens the panes.
- **Developers, at the cockpit.** Editor, terminal, files, git, notebook,
  database console, interpretability, training. These stay dense, keyboard-driven
  and unapologetically technical.

Third-party plugin authors are a real but secondary audience: the frontend SDK
(`@horribledashboard/sdk`), `backend.sdk`, and the `dash` REPL are public
contracts, and the marketplace is where their work arrives.

## Product Purpose

A unified one-stop app for everything — "emacs for the agentic era". One dockable
workspace of panes in which a **local-model agent is a first-class citizen**: it
opens windows, reads the user's library, drives a real browser, queries their
databases, plays games against friends' agents, and phones other people's nodes.

Success is that a user stops assembling a toolchain. The app is the environment,
the agent is a participant in it rather than a chat box bolted to the side, and
everything the agent can do the user can also do by hand — and vice versa.

## Positioning

Three things a neighboring product could not truthfully copy without rebuilding
itself around them:

1. **The agent drives the UI, not a sidebar.** A backend tool-calling loop over
   local models with layout control as its first capability. Panes, workspaces
   and every module's tool surface are addressable by the agent, gated by a
   permission engine for side effects.
2. **Local-first and node-shaped.** Weights, credentials, library, history and
   identity live on the user's machine. Credentials are Fernet-encrypted
   server-side and _never_ handed to the browser; the master key lives outside
   the data dir. The node can also serve its own weights (llama.cpp) rather than
   depending on somebody else's app.
3. **One module contract, all the way out.** Every built-in feature registers
   panes, commands, keybindings, agent tools and settings through the same
   registry that third-party plugins use. The extensibility story is not a
   parallel, lesser API.

Beyond that: a peer-to-peer fabric where users' _agents_ collaborate, and a
competitive games platform where the human competes by engineering the agent's
harness rather than by playing.

## Operating Context

- The app is a **frame**: roles of document / tool / widget, a center grid,
  activity rails left and right, a bottom dock, and named workspaces switched
  from a top tab strip. Widgets are first-class panes, not a separate grid.
- A desktop mode layers an OS-style shell on that frame — windows, edge snapping,
  taskbar, start menu, spotlight, wallpaper.
- The keyboard is a single authority (`packages/core/src/keymap/`), not
  per-component listeners. Panes that need the keyboard take capture; Escape
  unwinds one ordered ladder.
- Sessions are long-lived. Panes hold expensive live resources — PTYs, kernels,
  matches, browser sessions — so a workspace switch unmounts a pane without
  closing what it owns.
- Heavy capabilities are optional extras, lazy-imported, and degrade with a stated
  reason rather than failing blank.

## Capabilities and Constraints

Shipped surfaces include: agent (orchestrator, interpretability, MCP, flow canvas,
evals), dashboard/widgets, library, database console, symdex, connectors, editor,
terminal, files, git, notebook, browser, search, visualizer, karaoke, audio,
llamacpp, training, localtrack, hardware, network, social, commons, games,
hassault, marketplace, settings, observability. A full chat cockpit and some
cockpit polish remain unimplemented — see `docs/`.

Durable constraints:

- **Content is never bundled.** Other people's copyrighted media (AssaultCube
  assets, SearXNG, song files) is supported from the user's own install or fetched
  to their node — never shipped. Where content is needed, it is procedural,
  geometric or synthesized.
- **Secrets are never settings.** `GET /api/settings` hands the whole bag to the
  browser. API keys and client secrets belong in a connector; a client id is fine.
- **Every colour, radius and elevation is a CSS token** under a `data-theme`
  switch. A hardcoded literal is invisible to the theme switcher — there is a test
  that enforces this.
- **The backend is the source of truth for shared types.** Pydantic models at every
  API boundary; numbers the client needs are served, never duplicated in TS.
- **A capability probe reports three states**, not two: found it, looked and found
  nothing, and could not ask. Rendering "could not ask" as "not present" is a
  failure this product explicitly designs against.
- Docs are a contract: a change to a module, pane, command, route or shell
  behavior updates its `docs/` page in the same change.

Undecided / not established: licensing, pricing, distribution model, and whether
there is a public hosted offering at all. Future work must not invent any of these.

## Brand Commitments

Binding, confirmed by the user:

- **The name is `horrible-dashboard`** — always lowercase, always hyphenated.
  The tagline in use is "emacs for the agentic era".
- **The logo mark is fixed.** `assets/logo.svg` — the red `H` with the green status
  dot on a dark rounded square. It is not to be redrawn. `assets/banner.svg` and
  `assets/logo.png` are its existing derivatives.

Not binding: the README's dry, self-deprecating technical voice is the incumbent
tone, but the user did **not** commit future copy to it. Treat it as evidence of
current practice, not as a constraint.

## Evidence on Hand

- `README.md`, `CLAUDE.md`, and the `docs/` tree (one MDX page per module,
  published as a Docusaurus site under `website/`) — extensive, current, and the
  richest source of product truth in the repo.
- `assets/logo.svg`, `assets/banner.svg`, `assets/logo.png`, `assets/dancing.gif`
  (a demo recording).
- A live game server deployed on Fly (`horrible-games`).

Absent, and not to be fabricated: no LICENSE file, no testimonials, no customers,
no users beyond the author, no benchmarks, no press, no pricing, and no uptime or
adoption claims.

## Product Principles

1. **The agent is a participant, not a panel.** Anything a user can do, the agent
   should be able to do through the same contract — and every agent action should
   be visible and reversible in the UI it just changed.
2. **The node owns its data.** Local-first is a design constraint, not a feature
   bullet: nothing leaves the machine without the user asking, and secrets never
   cross to the browser.
3. **One contract, no privileged insiders.** Built-in modules use exactly the
   surface third-party plugins get. If the internal path is nicer, that's a bug.
4. **Degrade loudly, never silently.** Missing hardware, an absent optional extra,
   a probe that couldn't run — each says what happened and why, with a reason a
   user can act on. Silent wrongness is the failure mode this product fears most.
5. **Density is a feature at the cockpit, and a cost at the front door.** The home
   surface must stay approachable to someone who never opens a terminal; the tool
   panes are free to be as dense as the work is.

## Accessibility & Inclusion

**Undecided.** No accessibility standard has been established for this project.
Future work must not claim WCAG conformance, and must not assume one was agreed.
Existing keyboard-first behavior (a single keymap authority, full keybinding
coverage) is current practice, not a ratified commitment.
