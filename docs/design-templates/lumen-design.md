---
name: lumen-design
version: alpha
description: >
  Lumen is a near-black, warm-charcoal landing page for an ambient desktop AI,
  built around a single molten-orange voltage (#f97316) and an editorial serif.
  The signature move is restraint plus cinema: a full-bleed Spline 3D scene
  bleeding off the right edge of the hero, Instrument Serif headlines (with
  italic accent words) set against DM Sans body copy, and a scroll-scrubbed
  sequence of pinned sections — hero dissolve, word-by-word statement reveal,
  horizontal-scroll principles, and a product window that scales up to fill the
  frame. Geometry is soft and small: 6–12px radii, hairline borders instead of
  shadows, a fixed film-grain overlay, and a thin accent scroll-progress thread.
colors:
  # Brand / Accent
  accent: '#f97316'                       # the one voltage — eyebrows, italic accent words, CTAs, rails, scroll thread
  chip-primary-text: '#fde7d0'            # warm cream text on the primary action chip
  status-online: '#5ad078'               # green pulse dot in the product window status bar
  # Surface
  bg: '#0d0a08'                           # page background — warm near-black
  surface: '#14110d'                      # window chrome, kbd keys, raised surfaces
  # Text
  fg: '#fafafa'                           # headings + primary body
  fg-muted: 'rgba(250, 250, 250, 0.55)'   # subtext, captions, muted labels
  # Hairlines
  border: 'rgba(250, 250, 250, 0.12)'     # default hairline — nav, window, footer
  border-strong: 'rgba(250, 250, 250, 0.18)' # ghost button + input borders, kbd
  hairline-faint: 'rgba(255, 255, 255, 0.04)' # internal window dividers, tab fills
  # Accent washes (derived from accent at low alpha)
  accent-wash: 'rgba(249, 115, 22, 0.08)' # active rail item, shorthand key bg
  accent-chip: 'rgba(249, 115, 22, 0.14)' # primary chip fill
  # Syntax (code-diff mock)
  diff-removed: 'rgba(255, 145, 145, 0.9)' # removed lines in the diff block
  syntax-keyword: 'rgba(249, 175, 100, 0.95)' # keywords (warm amber)
  syntax-string: 'rgba(160, 220, 170, 0.9)'   # strings (green)
  syntax-number: 'rgba(180, 200, 255, 0.9)'   # numbers (blue)
  # Gradient
  hero-wash: 'linear-gradient(90deg, #0d0a08 0%, rgba(13,10,8,0.82) 26%, rgba(13,10,8,0.38) 46%, transparent 62%)' # left legibility wash over Spline
typography:
  display:
    fontFamily: "'Instrument Serif', 'Times New Roman', Georgia, serif"
    fontSize: clamp(80px, 8vw, 130px)
    fontWeight: 400
    lineHeight: 1.02
    letterSpacing: -0.02em
  statement:
    fontFamily: "'Instrument Serif', 'Times New Roman', Georgia, serif"
    fontSize: clamp(48px, 5.6vw, 84px)
    fontWeight: 400
    lineHeight: 1.05
    letterSpacing: -0.02em
  product-h2:
    fontFamily: "'Instrument Serif', 'Times New Roman', Georgia, serif"
    fontSize: clamp(32px, 4vw, 48px)
    fontWeight: 400
    lineHeight: 1.1
    letterSpacing: -0.015em
  principle-number:
    fontFamily: "'Instrument Serif', 'Times New Roman', Georgia, serif"
    fontSize: 56px
    fontWeight: 400
    lineHeight: 1
    fontStyle: italic
  principle-title:
    fontFamily: "'DM Sans', -apple-system, sans-serif"
    fontSize: 24px
    fontWeight: 500
    lineHeight: 1.25
    letterSpacing: -0.01em
  body:
    fontFamily: "'DM Sans', -apple-system, sans-serif"
    fontSize: 17px
    fontWeight: 400
    lineHeight: 1.5
  eyebrow:
    fontFamily: "'DM Sans', -apple-system, sans-serif"
    fontSize: 12px
    fontWeight: 500
    lineHeight: 1
    letterSpacing: 0.18em
    textTransform: uppercase
  mono:
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace'
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.6
rounded:
  xs: 4px      # tags, chips-inner, rail items, code inline
  sm: 5px      # kbd, chips, window tabs
  md: 6px      # buttons, inputs, logo mark, diff block
  lg: 12px     # window chrome
  full: 9999px # dots, poster spinner, status dot
spacing:                # base unit 4px; layout rhythm in rem (8px grid via 0.5rem steps)
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  2xl: 32px
  section: 8rem          # default vertical section padding
components:
  btn-primary:
    backgroundColor: '{colors.accent}'
    textColor: '{colors.bg}'
    typography: '{typography.body}'
    rounded: '{rounded.md}'
    padding: '0 24px (height 40px)'
    # hover: filter brightness(1.1)
  btn-ghost:
    backgroundColor: 'transparent'
    textColor: '{colors.fg}'
    border: '1px solid {colors.border-strong}'
    rounded: '{rounded.md}'
    padding: '0 24px (height 40px)'
  eyebrow:
    textColor: '{colors.accent}'
    typography: '{typography.eyebrow}'
    # rendered with a leading em-dash, e.g. "— Introducing"
  window-chrome:
    backgroundColor: '{colors.surface}'
    border: '1px solid {colors.border}'
    rounded: '{rounded.lg}'
    padding: '0 (internal panes self-pad)'
  kbd:
    backgroundColor: '{colors.surface}'
    textColor: '{colors.fg}'
    border: '1px solid {colors.border-strong} (bottom 2px)'
    rounded: '{rounded.sm}'
    padding: '0 6px (height 22px)'
  chip:
    backgroundColor: '{colors.hairline-faint}'
    textColor: '{colors.fg}'
    border: '1px solid rgba(255,255,255,0.08)'
    rounded: '{rounded.sm}'
    padding: '6px 12px'
  chip-primary:
    backgroundColor: '{colors.accent-chip}'
    textColor: '{colors.chip-primary-text}'
    border: '1px solid rgba(249,115,22,0.32)'
    rounded: '{rounded.sm}'
    padding: '6px 12px'
  tag:
    backgroundColor: 'rgba(255,255,255,0.04)'
    textColor: '{colors.fg-muted}'
    border: '1px solid rgba(255,255,255,0.08)'
    rounded: '{rounded.xs}'
    padding: '4px 10px'
  cta-input:
    backgroundColor: 'rgba(255,255,255,0.04)'
    textColor: '{colors.fg}'
    border: '1px solid {colors.border-strong}'
    rounded: '{rounded.md}'
    padding: '0 14px (height 40px)'
    # focus: border accent, bg lifts to 0.06
  site-nav:
    backgroundColor: 'rgba(13,10,8,0.88) + backdrop-blur(6px)'
    border: 'bottom 1px solid {colors.border}'
    typography: '{typography.body}'
    padding: '0 var(--pad-x) (height 56px)'
motion:
  ease: 'cubic-bezier(0.22, 1, 0.36, 1) — the global --ease used everywhere'
  hero-word-in: 'H1/H2 words fade + rise (translateY 10px) staggered 100ms each, 720ms'
  hero-dissolve: 'scroll-scrubbed: copy fades/rises, Spline fades + scales to 0.94 as hero scrolls'
  statement-scrub: 'words scrub opacity 0.32 → 1 word-by-word, caption fades up after'
  principles-horizontal: 'pinned section, track translateX 0 → -200vw on scroll (3 full-viewport slides)'
  product-scale: 'pinned window scrubs scale 0.78 → 1.0 over first 30% of pin'
  status-pulse: 'status dot opacity 1 ↔ 0.45, 2.4s infinite'
  spin: 'poster loading ring, 900ms linear infinite'
  transitions: '200ms hovers (buttons, chips, links); underline draw-in on principle titles + links'
  scroll-thread: 'fixed 1.5px accent bar, scaleY = scroll progress'
icons:
  library: inline SVG (checkmarks) + Unicode glyphs (◆ logo, ⌘ . K kbd, → arrows)
  style: minimal line / glyph
  stroke: 1.75px (checkmark)
  sizes: { sm: 14px, md: 22px }
  color: '{colors.accent}'
---

# Lumen

`lumen-design · alpha`

> Design spec — version alpha. Source: static HTML (`index.html`); tokens read
> from inline styles + class rules. Fictional/sample product; described factually.

## Overview
Lumen is the landing page for an ambient desktop AI, and it sells "restraint" as
a feature. The surface is a warm near-black (`--bg #0d0a08`) with a slightly
lighter charcoal (`--surface #14110d`) for raised chrome, lit by a single
voltage: molten orange `--accent #f97316`. Type carries the whole personality —
Instrument Serif sets every headline at editorial scale (`clamp(80px, 8vw,
130px)` in the hero), and the page's signature gesture is the **italic accent
word** in orange ("*desktop*", "*thinking*", "*room*") that lands the end of each
big line; DM Sans handles body and UI at a calm 17px. The hero is cinematic: a
full-bleed Spline 3D scene bleeds off the right viewport edge behind the copy,
with a left-to-right legibility wash so text stays readable. Below the fold the
page becomes a scroll-scrubbed film — pinned sections that dissolve the hero,
reveal the statement word-by-word, scroll three principles horizontally, and
scale a realistic product window up to fill the frame. Throughout, depth comes
from hairlines (not shadows), a fixed film-grain overlay at 5% opacity, a thin
accent scroll-progress thread, and small 6–12px radii.

## Colors
**Brand / accent.** Everything voltage-colored is `--accent #f97316`: the
em-dash eyebrows, the italic accent words, the primary buttons, the active rail
indicator, the scroll-progress thread, statement/section rules, and the cursor
ring. Low-alpha derivations carry it into fills — `rgba(249,115,22,0.08)` for the
active rail row and shorthand keys, `rgba(249,115,22,0.14)` for the primary
chip. The primary chip's text is a warm cream `#fde7d0`.

**Surface.** Page is `--bg #0d0a08` (warm near-black). Raised chrome — the
product window, `kbd` keys — uses `--surface #14110d`. Internal window panes use
near-black overlays `rgba(0,0,0,0.18–0.28)`.

**Text.** Headings and primary body are `--fg #fafafa`; secondary copy,
captions, and labels use `--fg-muted rgba(250,250,250,0.55)`.

**Hairlines.** `--border rgba(250,250,250,0.12)` is the default (nav bottom,
window outline, footer top). `--border-strong rgba(250,250,250,0.18)` is for
interactive edges (ghost button, input, kbd). Internal window dividers drop to
`rgba(255,255,255,0.04)`.

**Semantic.** A green `#5ad078` status dot signals "lumen ready". The code-diff
mock uses a small syntax set: removed lines `rgba(255,145,145,0.9)` on a faint
red wash, keywords amber `rgba(249,175,100,0.95)`, strings green
`rgba(160,220,170,0.9)`, numbers blue `rgba(180,200,255,0.9)`.

**Gradient.** The hero's left legibility wash is a horizontal
`linear-gradient(90deg, bg → transparent at 62%)`; section rules and the hero
edge-fade are subtle radial/linear accent gradients at low alpha.

## Typography
Two families, both loaded from Google Fonts:
**Instrument Serif** (display, regular + italic) and **DM Sans** (body, weights
400/500/600). Monospace UI (file rails, tabs, diffs, shorthand) uses the system
stack `ui-monospace, SFMono-Regular, Menlo`. Both display and body faces are
open-source, so no substitution is needed.

| Token | Size | Weight | Line-height | Tracking | Usage |
|---|---|---|---|---|---|
| display | clamp(80–130px) | 400 | 1.02 | -0.02em | Hero H1 (Instrument Serif) |
| statement | clamp(48–84px); up to 140px when pinned | 400 | 1.05 | -0.02em | Statement + closing CTA lines |
| product-h2 | clamp(32–48px) | 400 | 1.1 | -0.015em | Product section heading |
| principle-number | 56px (up to 360px pinned) | 400 italic | 1 | — | Principle numerals (orange, tabular) |
| principle-title | 24px | 500 | 1.25 | -0.01em | Principle headings (DM Sans) |
| body | 17px | 400 | 1.5 | — | Subtext, captions, base |
| eyebrow | 12px | 500 | 1 | 0.18em | Uppercase em-dash eyebrows (orange) |
| mono | 12px | 400 | 1.6 | — | Rails, tabs, diffs, shorthand, status |

## Layout
Base unit is 4px; layout rhythm runs in rem on a roughly 8px grid (0.5rem
steps). Horizontal padding is a CSS var `--pad-x` (2rem mobile → 4rem desktop).
The page is a single scroll-driven narrative: a 100svh hero (copy column + a
Spline column that bleeds off the right edge), then four pinned "moves" wrapped
in tall `*-pin` spacers — statement (180vh pin, sticky), principles (300vh pin,
horizontal-scroll track 300vw wide), product (200vh pin, scaling window), and a
normal closing CTA. Content max-width is 1100px (`.section-inner`); the hero copy
goes to 880px. Each section carries an editorial spine — a top-left
`— 0n / 04` index marker and a top-right italic marginalia note — plus a thin
centered accent rule between sections. Whitespace is generous and varied per
section (6–10rem blocks) to avoid a stack-of-boxes feel.

## Elevation & Depth
Depth is almost entirely hairlines and overlays — there are **no drop shadows**.
The nav and floating chrome use `backdrop-filter: blur(6px)` over a translucent
bg. The product window is a flat `--surface` card with a `--border` outline and
faint internal dividers. A fixed film-grain SVG overlay (5% opacity) sits above
everything for a photographed, tactile quality. The hero gets a left legibility
wash and a right radial edge-fade so the Spline bleed reads as intentional. The
only "lift" cue is the `kbd` key's 2px bottom border.

## Shapes
Soft and small. Radii: 4px for tags/rail-items/inline-code, 5px for kbd/chips/
tabs, 6px for buttons/inputs/logo-mark/diff blocks, 12px for the window chrome,
and full circles for dots, the status dot, and the loading spinner. Nothing is
sharp-cornered and nothing is heavily rounded — the geometry stays quiet so the
serif type and the single orange voltage carry the expression.

## Components
- **btn-primary** — `--accent` fill, `--bg` text, 6px radius, 0 24px / 40px tall; hover brightens. Labels: "Get started", "Get early access".
- **btn-ghost** — transparent, `--border-strong` outline, `--fg` text, 6px. Label: "See it think".
- **btn-sm** — nav variant, 34px tall, 0 14px.
- **eyebrow** — orange uppercase 12px with 0.18em tracking, rendered with a leading em-dash ("— Introducing").
- **site-nav** — fixed, 56px, `rgba(13,10,8,0.88)` + blur(6px), bottom hairline; hidden until past ~85% of the hero, then slides down.
- **window-chrome** — `--surface` card, `--border`, 12px radius; tab bar (traffic dots + active tab), 200px file rail + main pane, status bar.
- **kbd** — `--surface`, `--border-strong` with 2px bottom, 5px; renders ⌘ . K.
- **chip / chip-primary** — action buttons in the mock; faint white fill, or orange-wash primary with cream text.
- **tag** — context pills (`current file`, `terminal`), faint fill, 4px radius.
- **cta-input** — faint white fill, `--border-strong`, 6px; focus turns border orange. Placeholder "you@company.com".
- **mock-diff** — near-black block with syntax-colored removed lines.

## Motion
Everything eases on `cubic-bezier(0.22, 1, 0.36, 1)` (`--ease`). On load, H1/H2
words fade and rise, staggered 100ms each (720ms `word-in`). The rest is
scroll-scrubbed via `requestAnimationFrame` against cached layout positions:
the hero dissolves (copy fades/rises, Spline fades and scales to 0.94); the
statement scrubs its words from 0.32 → 1 opacity one at a time, then fades the
caption up; the principles track translates 0 → -200vw across a pinned 300vh
section (three full-viewport slides); the product window scrubs scale 0.78 → 1.0.
A fixed 1.5px accent thread tracks total scroll progress (`scaleY`). Ambient
loops: the status dot pulses (2.4s) and the poster ring spins (900ms). Hover
micro-interactions: 200ms button/chip color shifts, underline draw-ins on
principle titles and links, principle numerals lift 4px. An accent cursor ring
follows the pointer inside the product window. There's no `prefers-reduced-motion`
guard (the `reduceMotion` flag is hard-set to `false`).

## Iconography
No icon library. Icons are a single inline SVG checkmark (1.75px stroke, accent
color, 14px) in the action checklist, plus Unicode glyphs used decoratively and
in UI: the ◆ logo mark, ⌘ / . / K inside kbd keys, and → arrows in shorthand
rows and links. Window "traffic" controls and rail bullets are plain CSS shapes
(circles and 7px squares), not icons. Color is accent for active/semantic marks,
muted white otherwise.

## Do's and Don'ts
- **Do** keep orange as the only chromatic voltage — one accent, everything else warm-neutral.
- **Do** end big serif lines with a single italic orange accent word.
- **Do** lead eyebrows with an em-dash and uppercase them at 0.18em tracking.
- **Do** use hairlines and overlays for depth; keep surfaces flat.
- **Do** keep radii small (4–12px) and let type carry the expression.
- **Don't** add drop shadows, second accent hues, or heavily rounded shapes.
- **Don't** set headlines in DM Sans — the serif is the brand.
- **Don't** make the Spline scene a contained box; it must bleed off the edge.
- **Don't** over-saturate the accent washes; they live at 8–22% alpha.

## Known Gaps
- The Spline 3D scene is loaded from an external `.splinecode` URL; its colors,
  lighting, and composition are not in the HTML and are not tokenized here.
- Spacing tokens are inferred from the rem values actually used; the system mixes
  a 4px micro-grid with rem-based section rhythm rather than one strict scale.
- Several accent/syntax colors are low-alpha rgba derived from the base accent;
  listed values are exact from the CSS but read as tints, not flat hex.
- The `--font-display` for principle numerals is Instrument Serif italic; sizes
  scale dramatically (56px → up to 360px) only inside the pinned horizontal view.