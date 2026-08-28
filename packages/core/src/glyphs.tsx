/**
 * The shared icon set.
 *
 * DESIGN.md's rule is that an in-pane icon is a drawn vector inheriting
 * `currentColor`, never a native emoji — the documented exception being a pane
 * manifest's `icon:` field, which is the activity rail's own convention. The rule
 * was widely broken for one boring reason: there was nowhere to get an icon from,
 * so every pane reached for the glyph on its keyboard.
 *
 * These live in `core` rather than `ui` because core modules need them and
 * `core → ui` would be an import cycle.
 *
 * All of them are drawn on a 16×16 box at a 1.5 stroke so they sit on one optical
 * weight beside each other, take their size from `--icon-size` (default 13px, the
 * size that lines up with `title` type), and take their colour from the caller.
 */
import type { SVGProps } from 'react';

import './glyphs.css';

function Glyph({ children, ...rest }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 16 16"
      // Sized by CSS (`.hd-icon` reads `--icon-size`), not by the width/height
      // attributes: a `var()` is not valid in an SVG geometry attribute and
      // fails silently to the 300×150 replaced-element default.
      className="hd-icon"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

/** Something needs attention. Carries the banner's own `--kind` colour. */
export function IconAlert(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="8" r="6.25" />
      <path d="M8 5v3.5M8 11h.01" />
    </Glyph>
  );
}

/** Submit / send. */
export function IconSend(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M3 8h8M7.6 4.4 11.2 8l-3.6 3.6" />
    </Glyph>
  );
}

/** Try again. An open arc plus a head, so it reads as a cycle at 13px. */
export function IconRetry(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M13 8a5 5 0 1 1-1.6-3.7" />
      <path d="M13.2 2.4v2.6h-2.6" />
    </Glyph>
  );
}

/** Create / add. */
export function IconPlus(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M8 3.5v9M3.5 8h9" />
    </Glyph>
  );
}

/** Destructive. A lid and a body — not a literal wastebasket, which turns to mud at 13px. */
export function IconTrash(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M3 4.5h10M6.5 4.5V3h3v1.5" />
      <path d="M4.5 4.5 5 13h6l.5-8.5" />
    </Glyph>
  );
}

/** Confirmed / present. */
export function IconCheck(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M3.5 8.5 6.5 11.5 12.5 5" />
    </Glyph>
  );
}

/** Find. */
export function IconSearch(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="7.2" cy="7.2" r="4.2" />
      <path d="m10.4 10.4 2.6 2.6" />
    </Glyph>
  );
}

/** Disclosure. Rotate it with a transform rather than swapping in a second glyph. */
export function IconChevron(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="m5.5 3.5 5 4.5-5 4.5" />
    </Glyph>
  );
}

/** Recently used. A clock, for the Start menu's Recent band. */
export function IconClock(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="8" r="6.25" />
      <path d="M8 4.5V8l2.4 1.6" />
    </Glyph>
  );
}

/**
 * Pinned — a watch that is armed.
 *
 * Filled rather than outlined, because it sits next to its own unpinned twin in a
 * long list and stroke weight alone is not enough contrast to scan down a column.
 */
export function IconPin(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M6 2h4l-.6 3.4 2.1 2.1H4.5l2.1-2.1L6 2Z" fill="currentColor" />
      <path d="M8 7.5V14" />
    </Glyph>
  );
}

/** Not pinned. The same silhouette, hollow — so the pair reads as one control. */
export function IconPinOff(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M6 2h4l-.6 3.4 2.1 2.1H4.5l2.1-2.1L6 2Z" />
      <path d="M8 7.5V14" />
    </Glyph>
  );
}
