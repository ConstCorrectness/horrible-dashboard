---
name: horrible-dashboard
description: A field console for the agentic era — dense, angular, keyboard-first, one accent used sparingly.
colors:
  bg: '#14161a'
  bg-raised: '#1d2026'
  bg-elevated: '#22262e'
  bg-hover: '#262a32'
  bg-inset: 'rgb(0 0 0 / 25%)'
  border: '#2e333d'
  border-strong: '#3b414c'
  text-strong: '#f0f2f5'
  text: '#d7dae0'
  text-dim: '#8a909c'
  text-faint: '#5c6169'
  accent: '#6ea8fe'
  accent-contrast: '#0b0d10'
  success: '#3fb950'
  warn: '#e2c08d'
  danger: '#e06c75'
  gold: '#f5b942'
typography:
  display:
    fontFamily: "Geist, 'Segoe UI', system-ui, sans-serif"
    fontSize: '19px'
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: '0.14em'
  headline:
    fontFamily: "Geist, 'Segoe UI', system-ui, sans-serif"
    fontSize: '15px'
    fontWeight: 500
    lineHeight: 1.35
    letterSpacing: 'normal'
  title:
    fontFamily: "Geist, 'Segoe UI', system-ui, sans-serif"
    fontSize: '11px'
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: '0.14em'
  body:
    fontFamily: "Geist, 'Segoe UI', system-ui, sans-serif"
    fontSize: '12.5px'
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: 'normal'
  label:
    fontFamily: "Geist, 'Segoe UI', system-ui, sans-serif"
    fontSize: '10.5px'
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: '0.1em'
  telemetry:
    fontFamily: "'JetBrains Mono', ui-monospace, 'Cascadia Mono', consolas, monospace"
    fontSize: '10.5px'
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: 'normal'
  micro:
    fontFamily: "'JetBrains Mono', ui-monospace, 'Cascadia Mono', consolas, monospace"
    fontSize: '9.5px'
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: '0.1em'
  editorial:
    fontFamily: "'Instrument Serif', Georgia, 'Times New Roman', serif"
    fontSize: 'clamp(1.5rem, 4vw, 2.75rem)'
    fontWeight: 400
    lineHeight: 1.1
    letterSpacing: 'normal'
rounded:
  sm: '4px'
  md: '6px'
  lg: '10px'
  xl: '14px'
spacing:
  '1': '2px'
  '2': '4px'
  '3': '6px'
  '4': '8px'
  '5': '12px'
  '6': '16px'
  '7': '24px'
  '8': '32px'
components:
  button-default:
    backgroundColor: '{colors.bg-raised}'
    textColor: '{colors.text}'
    typography: '{typography.body}'
    rounded: '{rounded.sm}'
    padding: '4px 12px'
    height: '30px'
  button-default-hover:
    backgroundColor: '{colors.bg-hover}'
    textColor: '{colors.text}'
  button-primary:
    backgroundColor: '{colors.accent}'
    textColor: '{colors.accent-contrast}'
    typography: '{typography.body}'
    rounded: '{rounded.sm}'
    padding: '4px 12px'
    height: '30px'
  button-ghost:
    backgroundColor: 'transparent'
    textColor: '{colors.text-dim}'
    typography: '{typography.body}'
    rounded: '{rounded.sm}'
    padding: '4px 12px'
    height: '30px'
  button-danger:
    backgroundColor: 'transparent'
    textColor: '{colors.danger}'
    typography: '{typography.body}'
    rounded: '{rounded.sm}'
    padding: '4px 12px'
    height: '30px'
  button-sm:
    backgroundColor: '{colors.bg-raised}'
    textColor: '{colors.text}'
    typography: '{typography.telemetry}'
    rounded: '{rounded.sm}'
    padding: '2px 6px'
  chip:
    backgroundColor: '{colors.bg-inset}'
    textColor: '{colors.text-dim}'
    typography: '{typography.micro}'
    rounded: '16px'
    padding: '2px 6px'
  chip-ok:
    textColor: '{colors.success}'
  chip-warn:
    textColor: '{colors.warn}'
  chip-fail:
    textColor: '{colors.danger}'
  chip-info:
    textColor: '{colors.accent}'
  row:
    backgroundColor: '{colors.bg-raised}'
    textColor: '{colors.text}'
    rounded: '0'
    padding: '7px 10px 7px 14px'
  row-hover:
    backgroundColor: '{colors.bg-hover}'
  card:
    backgroundColor: '{colors.bg-raised}'
    textColor: '{colors.text}'
    rounded: '{rounded.md}'
    padding: '12px 16px'
  input:
    backgroundColor: '{colors.bg-inset}'
    textColor: '{colors.text}'
    typography: '{typography.body}'
    rounded: '{rounded.sm}'
    padding: '4px 6px'
    height: '30px'
  field-label:
    textColor: '{colors.text-dim}'
    typography: '{typography.label}'
---

# Design System: horrible-dashboard

## Overview

**Creative North Star: "The Field Console"**

This is an instrument you operate, not a site you browse. The whole system is
built for a person sitting in front of a dense, docked workspace for hours,
reading state off a dozen panes at once and acting on it by keyboard. Every
decision follows from that: information is packed, hierarchy does the work that
whitespace does elsewhere, and personality comes from restraint plus one accent
used sparingly enough that it still means something when it appears.

The structural motif is the **clipped top-left corner** — a 9px notch on a row,
12px on a card, cut with `clip-path`. It makes a container read as machined and
angular without a symmetric bevel, which would read as a tag. Colour arrives on a
**2px leading rail** down the left edge, never as a perimeter. A faint repeating
28px hairline gives every surface a tooling texture at rest that disappears at
reading distance. Type splits into two jobs that must never be confused:
**identity** is uppercase Geist with heavy 0.14em tracking; **telemetry** — every
id, count, duration and float — is muted JetBrains Mono. A row scans as
label-then-figures at a glance because the two never share a treatment.

Nothing here is per-component invention. Six themes are declared as blocks of
primitives on `<html>`, and every colour, radius, elevation and font family in
the app resolves through them; there is a test that fails when a component
hardcodes a value. The spacing, type and motion scale is deliberately
theme-independent — a theme changes how the app looks, not how far apart things
sit — with radius as the one exception, because a radius _is_ a theme's
character.

**Key Characteristics:**

- Angular containers with a clipped top-left corner, never a symmetric bevel
- Accent on a leading edge or a crisp border — never a glowing perimeter
- Uppercase tracked identity against monospace muted telemetry
- One imposed control height (30px) so mixed form rows share a baseline
- Six themes, one token set; the component layer has no per-theme branches
- Motion is a brief staggered arrival, capped, and fully disabled under
  `prefers-reduced-motion`

## Colors

A near-black ground with low contrast between surfaces, structure carried by 1px
borders, and exactly one accent hue. The palette below is **midnight**, the
default theme that `:root` carries unqualified; the five named variants are
documented under _Theme Variants_ and swap these primitives wholesale.

### Primary

- **Signal Blue** (`--accent`): the app's single voice. It marks the one thing
  that matters right now — the primary filled button, every focus ring, the
  selected row, the `info` verdict, the snap preview. Its rarity is what makes it
  legible.
- **Contrast Ink** (`--accent-contrast`): text and iconography drawn _on_ a
  filled accent. Never `--text`: the accent is a light blue, and light-on-light
  is how a primary button loses its label. This is the one token that must invert
  in a light theme.

### Secondary

- **Achievement Gold** (`--gold`): medals, graded results, and the games
  ceremony type. Declared once globally rather than per theme — a medal is the
  same colour in every room — but still a token, never a literal.
- **Companion Accent** (`--accent-2`): the second hue in two-tone surfaces
  (aurora backdrop, gradients). Defaults to `--accent`, so a theme with no
  opinion still renders one coherent hue instead of a stray fallback colour.

### Tertiary — the status vocabulary

Four semantic colours are the app's entire non-neutral vocabulary. There is no
separate "info"; that is what the accent is for.

- **Ready Green** (`--success`): a healthy connection, a passing verdict, an
  `ok` chip or row rail.
- **Caution Sand** (`--warn`): a degraded or unverified state — an install with
  no published digest, an `unvalidated` field, a probe that could not run.
- **Fault Red** (`--danger`): failure, and destructive intent _before_ the
  click. A destructive control is visibly different at rest, not only in the
  confirm dialog after it.

### Neutral

- **Console Ground** (`--bg`): the page and pane-header ground.
- **Raised Surface** (`--bg-raised`): a row, a card, a window body.
- **Elevated Surface** (`--bg-elevated`): a selected row, a titlebar, a popover.
- **Hover Wash** (`--bg-hover`): the interactive-hover ground.
- **Recessed Well** (`--bg-inset`): code blocks, copyable URLs, input fields.
  Translucent black on purpose, so it darkens whatever surface it lands on rather
  than assuming it lands on `--bg`. In a light theme it must become a _light_
  wash, or a code block becomes a hole punched in the page.
- **Hairline** (`--border`) and **Hairline Strong** (`--border-strong`): the
  primary structural device. Most separation in this app is a 1px line, not a
  shadow.
- **Text ramp**: `--text-strong` for headings and the one or two values a pane
  exists to show; `--text` for nearly everything; `--text-dim` for telemetry and
  labels; `--text-faint` for hints and empty-state bodies.

### Named Rules

**The One Voice Rule.** The accent is the answer to "which one thing?" — one
filled primary action per surface, one focus ring at a time. A screen where three
elements are outlined in the accent has no primary action.

**The Leading Edge Rule.** A verdict is a 2px rail on the left edge plus a
matching corner notch, never a coloured perimeter. The row stays a row instead of
becoming a card.

**The One Vocabulary Rule.** `ok` / `warn` / `fail` / `info` mean the same colour
in a chip, a row rail, a row mark and a badge. A reader learns the colour once.

**The No Literal Rule.** A component never hardcodes a colour. If a needed colour
is absent, add the token to _every_ theme — a literal is invisible to the switcher
and silently survives into a theme it was never designed for. `design-tokens.test.ts`
enforces this.

### Theme Variants

Six themes ship. Each declares the full primitive set; the derived alias block at
the bottom of `themes.css` resolves against whichever is active, so a new theme
declares primitives only and inherits every alias for free.

- **midnight** (default) — hairline-led near-black. Structure from 1px borders,
  small radii (4/6/10/14), almost no elevation. Geist.
- **studio** — a sibling, not an inversion. Same ground and accent, but surfaces
  sit six steps apart and are held apart by _elevation and radius_ (6/10/16/20)
  instead of a bright hairline. DM Sans. Brightening either surface collapses it
  back into midnight.
- **glass** — translucent chrome over the desktop backdrop, real blur behind
  every window and the taskbar, large radii (8/12/18/24), and window controls on
  the left (macOS convention). The one theme where surfaces are declared with
  alpha.
- **hud** — terminal-cyberpunk. Every radius is 0 _on purpose_: it is the theme
  that proves the radius tokens are actually threaded, since a hardcoded
  `border-radius` is visible here and nowhere else. Depth comes from a glow, because
  a drop shadow on a near-black ground is invisible. Mono in the chrome too.
- **daylight** — the productivity light theme. `color-scheme: light` is
  load-bearing: it flips native scrollbars, form controls and the caret, which CSS
  cannot otherwise reach.
- **retro** — beveled 90s desktop. The `--win-border-light` / `--win-border-dark`
  pair is why the pair exists: `desktop.css` always assigns all four window border
  colours from it, so unequal values give a raised bevel and equal values give a
  flat edge, with no conditional anywhere.

## Typography

**Display / UI Font:** Geist (self-hosted variable; falls back to Segoe UI,
system-ui)
**Editorial Font:** Instrument Serif (self-hosted; falls back to Georgia)
**Mono Font:** JetBrains Mono (self-hosted variable; falls back to ui-monospace,
Cascadia Mono, Consolas)

**Character:** A precise neo-grotesque doing all the structural work, against a
mono that is genuinely load-bearing rather than decorative. The serif appears
rarely and ceremonially — a games hero, a result announcement — and its scarcity
is the point. Each face is self-hosted, because a font _stack_ does not choose a
font, it chooses whichever font the machine already has; the same log rendered in
Cascadia on one box and Menlo on another until JetBrains Mono was hosted and put
first.

### Hierarchy

- **Display** (700, 19px, 0.14em, uppercase): the largest identity type — a
  hero label, a section banner. Still uppercase and tracked; this system has no
  large lowercase heading.
- **Headline** (500, 15px): the lead line inside a pane — a title a user reads
  as a sentence rather than as a label.
- **Title** (700, 11px, 0.14em, uppercase): the workhorse. A row's identity, a
  pane header's title, an empty state's title. Small by design — its weight and
  tracking carry it, not its size.
- **Body** (400, 12.5px, 1.45): a row's body, a description, prose in a pane.
  Cap measure at ~46ch in centered contexts such as empty states.
- **Label** (700, 10.5px, 0.1em, uppercase): field labels. Muted, never
  `--text-strong`.
- **Telemetry** (400, 10.5px, mono, `--text-dim`): every id, count, duration,
  path, hash and float. Divided by a 1px hairline rule rather than a middot,
  because a middot reads as punctuation inside a number.
- **Micro** (700, 9.5px, mono, 0.1em, uppercase): chips and badges only.
- **Editorial** (400, clamp, Instrument Serif): ceremony type only.

### Named Rules

**The Two Jobs Rule.** Identity is uppercase tracked sans; telemetry is muted
mono. A figure that takes the title's treatment stops being scannable, and a
label that takes the mono treatment stops being a label. Never blend them.

**The Named-For-The-Job Rule.** Type tokens are named for what they do
(`--fs-label`, `--fs-meta`), not what size they are, because the job is what a
caller knows. The oddly precise sizes (10.5px, 12.5px) are measured values from
the incumbent row design; rounding them silently reflows every row in the app.

**The One Mono Rule.** `--font-mono` is deliberately _not_ per-theme. A theme
changes how the app looks, not which glyphs line up.

## Layout

The app is a **frame**, not a page: an activity rail on each side, a bottom dock,
a top workspace tab strip, and a center grid of panes that split, tab and float.
A desktop mode layers OS-style windows, snapping and a taskbar on the same frame.
Panes are resized by the user, so **the app has essentially no viewport
breakpoints** — only three exist in the entire stylesheet set (`width <= 720px`
in four places, `max-width: 620px` in one). Density adapts to the _pane_, via
intrinsic sizing, `min-width: 0` on every flex child, and `auto-fill` grids at a
240px minimum. Designing a new pane against viewport widths is a category error;
design it to survive being 320px wide in a split.

Spacing runs on a 2px base that opens up: **2 · 4 · 6 · 8 · 12 · 16 · 24 · 32**
(`--space-1` … `--space-8`). `--space-4` (8px) is the default gutter inside a
row; `--space-5` (12px) is the gap between cards, wider than a row's 4px gutter
because cards carry their own padding and would otherwise read as touching.

**The One Height Rule.** Every inline control settles on `--control-h` (30px). A
`<select>`, a text input and a small button have three different intrinsic
heights, so a form row built from all three is ragged unless one number is
imposed — and a row of controls that do not share a baseline is the loudest sign
of an unfinished form.

**The Scale-Or-Nothing Rule.** No literal sizes in new work. The app already
carries ~1,987 inline `style={{}}` objects written before the scale existed, each
picking its own `fontSize: 11` and `gap: 6`; that is precisely why two panes
docked side by side never lined up. Reach for a token or add one.

## Elevation & Depth

Depth is **per-theme by construction**, and the component layer never branches on
it. Components declare `--shadow-card` / `--shadow-overlay` (aliased to
`--elev-1` / `--elev-2`); each theme decides what those mean. Midnight is
hairline-led with barely any shadow at all — structure comes from 1px borders.
Studio holds a raised surface only six steps off the ground and separates it with
a real shadow. Hud lights the _edge_ with a glow, because a drop shadow on a
near-black ground is invisible. Retro draws a hard 2px offset block, because there
were no soft shadows in 1995. Daylight is the only theme with conventional
ambient elevation, because a bright ground is the only ground you can see a
shadow on.

### Shadow Vocabulary

- **`--elev-0`** (`none`): the default. Surfaces are flat at rest.
- **`--elev-1`** (`--shadow-card`, midnight: `0 4px 6px rgb(0 0 0 / 15%)`):
  cards, rows, raised panes.
- **`--elev-2`** (`--shadow-overlay`, midnight: `0 8px 24px rgb(0 0 0 / 40%)`):
  popovers, dialogs, floating windows.

A card additionally carries a 1px **inset top highlight**
(`inset 0 1px 0 color-mix(in srgb, var(--text) 7%, transparent)`) — the light
catching the lip of a raised surface. Mixed from `--text` rather than from white
so it stays a highlight on dark themes and a soft line on light ones; a hardcoded
white inset is invisible in half the themes and wrong in the rest.

### Named Rules

**The Three-Step Rule.** There are exactly three elevation steps, and each
aliases what a theme already declares. Inventing a middle shadow per theme
produces a ramp with a step no theme designed — one that looks wrong in five of
six themes.

**The Edge-Not-Bloom Rule.** Where a theme wants to signal depth with light, it
lights a hairline edge. It does not add a perimeter glow to a component.

## Shapes

Angular first. The signature is a **clipped top-left corner** cut with
`clip-path` — `9px` on a row, `12px` on a card, `4px` on the drawn checkbox. It
is asymmetric on purpose: a symmetric bevel reads as a tag, while a single
clipped corner reads as a horizontal record. Because the corner is clipped, the
accent rail must be a child pseudo-element inset past the notch (`top: 9px`),
never a `border-left` — a clipped border loses its own corner.

Radii come from the theme, not the component: `--radius-sm` (4px) on buttons and
inputs, `--radius-md` (6px) on cards, `--radius-lg` (10px) on windows,
`--radius-xl` (14px) on the largest containers. Rows are deliberately square
(`border-radius: 0`) — the notch is their corner treatment.

A chip is a pill derived from the theme's own radius
(`calc(var(--radius-sm) * 4)`), never pinned at `999px`. At ~20px tall that is
fully round in midnight and studio, while hud and retro keep the square tags
their theme authors chose. Hardcoding the pill would override a theme decision
app-wide, which is the exact thing the per-theme radius scale exists to prevent.

Surface texture is a single permitted pattern: a repeating 1px hairline every
28px, masked so it fades in from the left, at 40–50% opacity. It gives a surface
a tooling grain at rest and vanishes at reading distance.

## Components

The component layer is three files, each continuing the previous one's language
rather than reinventing it: `datalist.css` (a record you read), `primitives.css`
(the controls around it), `resourcecard.css` (a record you configure). All three
are expressed purely in scale tokens.

### Buttons

- **Shape:** small radius (`--radius-sm`), `min-height` pinned to `--control-h`
  (30px) so a button in a form row lines up with the input beside it rather than
  sitting a few pixels proud.
- **Default:** raised surface with a hairline border; hover moves to the hover
  wash and the strong border.
- **Primary:** _filled_ accent with contrast ink (4px 12px). Filled and not
  outlined, because a screen where three things are outlined in the accent has no
  primary action. Hover mixes 14% white into the accent.
- **Ghost:** transparent, no border, dim text; hover gains the hover wash and
  full-strength text. For secondary and repeated actions.
- **Danger:** a real intent, not red text — a translucent danger border at rest,
  filling to a 12% wash on hover. A destructive control must be visibly different
  _before_ the click.
- **Focus:** `2px solid var(--accent)` at `outline-offset: 1px`, on
  `:focus-visible` only, so a mouse click leaves no ring behind.
- **Disabled:** `opacity: 0.45` **and** `cursor: not-allowed`. Opacity alone
  still shows a pointer and a hover, which reads as "it didn't work" rather than
  "you can't".
- **Small (`data-size="sm"`):** 2px 6px padding at telemetry size; the height
  pin relaxes.

### Chips

- **Style:** a pill at micro size — mono, 700, uppercase, 0.1em tracking — on
  the recessed well ground with a hairline border.
- **Verdict states:** a **soft pill**, not a bare outline: the semantic colour as
  text, a 40% mix of it as border, a 12% mix as background. The wash is what keeps
  a 9.5px chip legible without shouting; an outline alone at that size reads as a
  text input, which is the wrong affordance entirely. All four are mixed from the
  semantic tokens, so a new theme gets every state free.
- **Dot modifier:** a 6px `currentcolor` circle. A connection state and a label
  are the same fact at two densities, so the dot is a modifier and not a second
  component.

### Cards / Containers

- **Corner:** `--radius-md` plus the 12px top-left clip.
- **Background:** raised surface with the inset top highlight and the 28px
  hairline grid at 40% opacity.
- **Rail:** a 2px leading edge inset to `top: 12px`, coloured by
  `--hd-card-accent`, animating in 120ms after the card lands.
- **Hover:** a crisp border at `color-mix(accent 40%, border)`. Not a glow.
- **Padding:** `12px 16px` — one number every internal band inherits, so a
  header, a form row and a footer all start on the same vertical line.
- **Bands:** identity → content → configuration → actions, always in that order.

### Rows (the signature component)

The most-used object in the app. A row is three separable jobs: **identity**
(uppercase tracked title), **telemetry** (mono, muted, hairline-divided figures),
and **verdict** (a `data-kind` rail plus corner notch). Square corners, 9px
clip, `7px 10px 7px 14px` padding, 4px gutter between siblings. Interactive rows
shift 2px right on hover; selected rows take the elevated surface and a 60% mix
of their verdict colour as border. Lists switch to an `auto-fill` grid at 240px
minimum when the items are peers being _picked from_ rather than records being
read in order.

### Inputs / Fields

- **Structure:** label + control + hint, with an **error that replaces the
  hint** rather than joining it — showing both makes the eye work out which line
  is current truth, and the hint has already been read by the time a value is
  wrong.
- **Style:** recessed well ground, hairline border, small radius, 30px min
  height. Controls are selected by _element_ inside `.hd-field`, so a `<select>`
  never needs the caller to remember a class name.
- **Focus:** outline removed, border becomes the accent. Invalid: border becomes
  danger, driven by `data-invalid` on the field.
- **Textarea:** mono at telemetry size, min two control-heights, vertical resize
  only. A textarea in this app is always code or a prompt, never prose.

### Checkbox, Radio, Switch

- **Checkbox / radio:** `appearance: none`, 1rem, drawn tick made of two
  rotated borders — no asset and no data URI, because a data-URI SVG cannot read
  a custom property. The **fill** is the state signal, not the tick: worst case is
  a checkbox without a tick, which is still unambiguous.
- **Switch:** `<button role="switch" aria-checked>`, not a styled checkbox. The
  role carries state to a screen reader _and_ drives the visual state, so the two
  cannot drift apart. Use it for any boolean that takes effect immediately —
  which is every boolean in this app, since there is nothing to submit.

### Pane Header

Title / figures / actions on one baseline-aligned bar above a pane's content,
with a bottom hairline and the page ground behind it. It shares the row's split
of jobs exactly, so a header and the rows beneath it read as one object rather
than as a heading that happens to sit above a list.

### Empty State

Centered stack at `--space-8` vertical padding, title small and _dim_ rather than
strong, body capped at 46ch in the faint tone. The rule the component exists to
enforce: **an empty state says what to do, not that there is nothing.** "No
servers" is a fact the user can already see; "Add one below, or browse the
registry" is the pane doing its job. The action sentence is where the design
intent lives, which is why the title is deliberately quiet.

### Motion

- **Durations:** `--dur-fast` 120ms (hover and state), `--dur-base` 220ms
  (entrance), `--dur-slow` 320ms.
- **Easing:** `--ease-standard: ease` for state, `--ease-entrance:
cubic-bezier(0.22, 0.61, 0.36, 1)` for arrivals.
- **Stagger:** `--stagger-step` 26ms per item, applied via `--hd-i`. It must stay
  small and it must be **capped by the caller** (`STAGGER_CAP`), because the delay
  is linear in list length — an unbounded stagger means a 200-row list is still
  arriving seconds after it rendered. The cap is what keeps it a flourish rather
  than a wait.
- **The arrival:** a row fades in from 6px left, then its rail draws itself
  downward 120ms later — the one moving part that says "this is a verdict" rather
  than "this is a container".
- **Rolling counters:** numeric stats animate to their value, but the counter is
  **seeded at its final value** with a timeout that snaps to it. `requestAnimationFrame`
  does not fire in a backgrounded tab, and a tile reading `0` when it means `62`
  is worse than no animation at all.

**The Reduced-Motion Rule.** Every animation and every hover transform is
disabled under `prefers-reduced-motion: reduce`. Seven such blocks exist; a new
animated component adds the eighth.

## Do's and Don'ts

### Do:

- **Do** resolve every colour, radius, elevation and font family through a token.
  If the token you need doesn't exist, add it to all six themes.
- **Do** put identity in uppercase tracked sans (11px/700/0.14em) and every
  figure in muted mono (10.5px). The split is the whole reason a row scans.
- **Do** carry a verdict on a 2px leading rail plus the corner notch, using the
  shared `ok` / `warn` / `fail` / `info` vocabulary.
- **Do** pin every inline control to `--control-h` (30px) so a mixed form row
  shares one baseline.
- **Do** use the spacing scale (2/4/6/8/12/16/24/32) rather than a literal, and
  8px as the default gutter inside a row.
- **Do** design a pane to survive at 320px wide in a split. Density adapts to the
  pane, not to the viewport.
- **Do** derive state colours with `color-mix` from the semantic tokens (40%
  border, 12% background) so a new theme inherits every state for free.
- **Do** disable animation and hover transforms under `prefers-reduced-motion`,
  and cap any stagger.
- **Do** make an empty state name the next action, not the absence.
- **Do** use `:focus-visible` with a 2px accent outline at 1px offset, and never
  remove a focus indicator without replacing it with a stronger one.

### Don't:

- **Don't** put a glowing perimeter border on anything. The accent rides a
  leading edge or a crisp 1px border; the full-perimeter bloom is out.
- **Don't** build a centered vertical stack of soft cards with heavy drop
  shadows. Favor asymmetric structured grids, technical dividers, and clipped
  containers.
- **Don't** use native OS emojis as UI icons or decorative badges inside a pane.
  Vector stroke icons that inherit `currentColor` only. The single documented
  exception is a pane manifest's `icon:` field, which is the activity rail's own
  convention.
- **Don't** hardcode a colour, a radius, or a `999px` pill. A pill is
  `calc(var(--radius-sm) * 4)`; hud and retro chose square corners and a literal
  overrides that decision app-wide.
- **Don't** give a figure the title's treatment, or a label the mono treatment.
- **Don't** round the measured type sizes (10.5px, 12.5px, 9.5px) — doing so
  reflows every row in the app.
- **Don't** signal disabled with opacity alone; add `cursor: not-allowed`.
- **Don't** show a hint and an error at the same time. The error replaces the
  hint.
- **Don't** use a `border-left` for a rail on a clipped container — a clipped
  border loses its own corner. Use an inset pseudo-element.
- **Don't** branch a component on the active theme. If a theme needs different
  behavior, it belongs in a token (the `--win-border-light`/`--win-border-dark`
  bevel is the model: one rule, two looks, no conditional).
- **Don't** invent a middle elevation step. There are three, and each aliases
  what the theme already declares.
