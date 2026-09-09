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

/** Put this on the clipboard. Two offset sheets — the copy and the original. */
export function IconCopy(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <rect x="6" y="6" width="7.5" height="7.5" rx="1.5" />
      <path d="M10 6V4a1.5 1.5 0 0 0-1.5-1.5H4A1.5 1.5 0 0 0 2.5 4v4.5A1.5 1.5 0 0 0 4 10h2" />
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

/** Dismiss / close. */
export function IconClose(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />
    </Glyph>
  );
}

/** A filled mark — an unsaved buffer, an unread item. Filled, not stroked: it is
 * a state, not an action, and a hollow ring reads as a disabled radio button. */
export function IconDot(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="8" r="3.25" fill="currentColor" stroke="none" />
    </Glyph>
  );
}

/** Remove one from a set — the counterpart of {@link IconPlus}. */
export function IconMinus(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M3.5 8h9" />
    </Glyph>
  );
}

/** A file. */
export function IconFile(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M9 1.75H4.5A1.25 1.25 0 0 0 3.25 3v10A1.25 1.25 0 0 0 4.5 14.25h7A1.25 1.25 0 0 0 12.75 13V5.5z" />
      <path d="M9 1.75V5.5h3.75" />
    </Glyph>
  );
}

/** A branch — source control. */
export function IconBranch(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="4.5" cy="3.5" r="1.75" />
      <circle cx="4.5" cy="12.5" r="1.75" />
      <circle cx="11.5" cy="6" r="1.75" />
      <path d="M4.5 5.25v5.5M11.5 7.75c0 2-1.5 3-3.5 3.25" />
    </Glyph>
  );
}

/**
 * Match case. The three search toggles are lettering rather than pictograms
 * because that is what they mean and what every editor draws — but drawn as
 * paths, so they take `currentColor` and the stroke weight of their siblings
 * instead of arriving as a font the theme did not choose.
 */
export function IconMatchCase(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M1.5 11.5 4.25 4.5 7 11.5M2.4 9.4h3.7" />
      <path d="M13 8.6a1.9 1.9 0 1 0 0 2.9v-3.3c0-1-.7-1.6-1.8-1.6-.8 0-1.4.3-1.8.8" />
      <path d="M13 11.5V8.2" />
    </Glyph>
  );
}

/** Match whole word. */
export function IconWholeWord(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M4.6 8.9a1.6 1.6 0 1 0 0 2.4V8.2c0-.85-.6-1.35-1.5-1.35-.7 0-1.2.25-1.55.7" />
      <path d="M4.6 11.3V8.5" />
      <path d="M7.6 4.5v6.8M7.6 8.6c.35-.5.9-.8 1.55-.8a1.75 1.75 0 0 1 0 3.5c-.65 0-1.2-.3-1.55-.8" />
      <path d="M1.5 13.5h13" />
    </Glyph>
  );
}

/** Use regular expression. */
export function IconRegex(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M10 3v6M7.4 4.5l5.2 3M12.6 4.5l-5.2 3" />
      <circle cx="4.25" cy="11.75" r="1.1" fill="currentColor" stroke="none" />
    </Glyph>
  );
}
