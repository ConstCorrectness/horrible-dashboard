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
import type { ReactElement, SVGProps } from 'react';

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

/** An upvote, or any count that means "people liked this". */
export function IconArrowUp(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M8 13V3.5M4 7.5l4-4 4 4" />
    </Glyph>
  );
}

/** Leaves the app: a link that opens in the system browser. */
export function IconExternal(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M9.5 2.75h3.75V6.5M13.25 2.75 7.5 8.5" />
      <path d="M11.5 9.5v3a.75.75 0 0 1-.75.75H3.5a.75.75 0 0 1-.75-.75V5.25a.75.75 0 0 1 .75-.75h3" />
    </Glyph>
  );
}

/** A discussion thread — a comment count. */
export function IconComment(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M2.75 4a1.25 1.25 0 0 1 1.25-1.25h8A1.25 1.25 0 0 1 13.25 4v5.5A1.25 1.25 0 0 1 12 10.75H7l-3 2.5v-2.5A1.25 1.25 0 0 1 2.75 9.5z" />
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

/** Stop a broadcast. A filled square, the transport-control convention. */
export function IconStop(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <rect x="4.5" y="4.5" width="7" height="7" rx="1" fill="currentColor" stroke="none" />
    </Glyph>
  );
}

/** A screen — sharing a display. */
export function IconScreen(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <rect x="2" y="2.75" width="12" height="8.5" rx="1.25" />
      <path d="M6 13.75h4M8 11.25v2.5" />
    </Glyph>
  );
}

/** A QR code. Three finder squares and a scatter, legible at 13px. */
export function IconQr(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <rect x="2.5" y="2.5" width="4" height="4" rx="0.5" />
      <rect x="9.5" y="2.5" width="4" height="4" rx="0.5" />
      <rect x="2.5" y="9.5" width="4" height="4" rx="0.5" />
      <path d="M9.5 9.5h1.5v1.5M13.5 9.5v.01M9.5 13.5h.01M12 12h1.5v1.5" />
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

/** Start playback. Filled, like {@link IconStop}: transport controls are states. */
export function IconPlay(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M5 3.5v9l7.5-4.5z" fill="currentColor" stroke="none" />
    </Glyph>
  );
}

/** Pause playback. */
export function IconPause(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <rect x="4" y="3.5" width="2.75" height="9" rx="0.6" fill="currentColor" stroke="none" />
      <rect x="9.25" y="3.5" width="2.75" height="9" rx="0.6" fill="currentColor" stroke="none" />
    </Glyph>
  );
}

/** Skip to the next item. */
export function IconSkipNext(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M3.5 4v8l6-4z" fill="currentColor" stroke="none" />
      <path d="M12 4v8" />
    </Glyph>
  );
}

/** Back to the previous item (or the start of this one). */
export function IconSkipPrev(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M12.5 4v8l-6-4z" fill="currentColor" stroke="none" />
      <path d="M4 4v8" />
    </Glyph>
  );
}

/** Shuffle: two crossing paths. */
export function IconShuffle(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M2 4.5h2.5c3 0 4 7 7 7H14M2 11.5h2.5c1.2 0 2-1.1 2.7-2.4M9.3 6.9c.7-1.3 1.5-2.4 2.7-2.4H14" />
      <path d="M12.25 2.75 14 4.5l-1.75 1.75M12.25 9.75 14 11.5l-1.75 1.75" />
    </Glyph>
  );
}

/** Repeat: a loop. Pass `one` for repeat-one. */
export function IconRepeat({ one, ...props }: SVGProps<SVGSVGElement> & { one?: boolean }) {
  return (
    <Glyph {...props}>
      <path d="M3 7.5V6.25A1.75 1.75 0 0 1 4.75 4.5H13M11.25 2.75 13 4.5l-1.75 1.75" />
      <path d="M13 8.5v1.25a1.75 1.75 0 0 1-1.75 1.75H3M4.75 13.25 3 11.5l1.75-1.75" />
      {one && <path d="M7.4 7.2 8.2 6.6v3.2" strokeWidth="1.2" />}
    </Glyph>
  );
}

/** Radio / continuous play: broadcast arcs around a point. */
export function IconRadio(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="8" r="1.25" fill="currentColor" stroke="none" />
      <path d="M5.2 5.2a4 4 0 0 0 0 5.6M10.8 5.2a4 4 0 0 1 0 5.6M3.4 3.4a6.5 6.5 0 0 0 0 9.2M12.6 3.4a6.5 6.5 0 0 1 0 9.2" />
    </Glyph>
  );
}

/** Volume. Pass `muted` for the struck-through speaker. */
export function IconVolume({ muted, ...props }: SVGProps<SVGSVGElement> & { muted?: boolean }) {
  return (
    <Glyph {...props}>
      <path d="M2.5 6.25h2.25L8 3.5v9L4.75 9.75H2.5z" />
      {muted ? (
        <path d="M10.5 6l3 4M13.5 6l-3 4" />
      ) : (
        <path d="M10.5 5.75a3 3 0 0 1 0 4.5M12.25 4a5.5 5.5 0 0 1 0 8" />
      )}
    </Glyph>
  );
}

/** A piece of music — the empty-art placeholder. */
export function IconMusic(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M6 11.5V3.5l7-1.25v8" />
      <circle cx="4.5" cy="11.5" r="1.75" />
      <circle cx="11.5" cy="10.25" r="1.75" />
    </Glyph>
  );
}

/** Bring something in — upload / import. */
export function IconImport(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M8 2.5v7.5M5 7l3 3 3-3" />
      <path d="M2.75 10.5v2.25h10.5V10.5" />
    </Glyph>
  );
}

/** A list of items — a queue. */
export function IconList(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M5.5 4h8M5.5 8h8M5.5 12h8" />
      <path d="M2.5 4h.01M2.5 8h.01M2.5 12h.01" strokeWidth="2" />
    </Glyph>
  );
}

/** More actions — the row's overflow menu. Dots, filled: they are targets, not lines. */
export function IconMore(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="3.5" cy="8" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="8" cy="8" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="12.5" cy="8" r="1.1" fill="currentColor" stroke="none" />
    </Glyph>
  );
}

/** A person — add a friend, someone's profile. */
export function IconPerson(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="5.25" r="2.5" />
      <path d="M3 13.25c.6-2.5 2.6-4 5-4s4.4 1.5 5 4" />
    </Glyph>
  );
}

// ── Research surfaces ────────────────────────────────────────────────────────
// Drawn for the interpretability suite's rail, but named for what they depict so
// any pane can use them.

/** A home / overview. */
export function IconHome(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M2.75 7.25 8 2.75l5.25 4.5" />
      <path d="M4.25 6v7.25h7.5V6" />
    </Glyph>
  );
}

/** A stack of prompt blocks — the context window. */
export function IconLayers(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M8 2.5 13.5 5.25 8 8 2.5 5.25Z" />
      <path d="M2.5 8 8 10.75 13.5 8" />
      <path d="M2.5 10.75 8 13.5l5.5-2.75" />
    </Glyph>
  );
}

/** Steps of a run — an agent turn, a recorded trace. */
export function IconTimeline(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M3.5 2.5v11" />
      <circle cx="3.5" cy="4.5" r="1" fill="currentColor" stroke="none" />
      <circle cx="3.5" cy="8" r="1" fill="currentColor" stroke="none" />
      <circle cx="3.5" cy="11.5" r="1" fill="currentColor" stroke="none" />
      <path d="M6.5 4.5h7M6.5 8h5M6.5 11.5h6" />
    </Glyph>
  );
}

/** A model's block structure — the architecture. */
export function IconCube(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M8 2.25 13.25 5v6L8 13.75 2.75 11V5Z" />
      <path d="M2.75 5 8 7.75 13.25 5M8 7.75v6" />
    </Glyph>
  );
}

/** Attention: a query reaching back to several keys. */
export function IconAttention(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="12.5" cy="12.5" r="1.25" />
      <circle cx="3.5" cy="3.5" r="1" />
      <circle cx="3.5" cy="8" r="1" />
      <circle cx="3.5" cy="12.5" r="1" />
      <path d="M11.4 11.7 4.4 4.3M11.25 12.1 4.5 8.4M11.25 12.5H4.5" />
    </Glyph>
  );
}

/** Draw / design. */
export function IconPencil(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M10.5 2.75 13.25 5.5 5.5 13.25H2.75V10.5Z" />
      <path d="M9 4.25 11.75 7" />
    </Glyph>
  );
}

/** A recorded forward pass: a waveform through layers. */
export function IconWave(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M1.75 8h2l1.5-4.5 2.5 9 2.5-6.5 1.25 2h2.75" />
    </Glyph>
  );
}

/** Step through, debugger-style. */
export function IconStep(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M3 4.5h6.5a3 3 0 0 1 0 6H5" />
      <path d="M6.75 8.5 4.75 10.5l2 2" />
      <path d="M12.75 3v2.5" />
    </Glyph>
  );
}

/** A lens reading the stream. */
export function IconLens(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <ellipse cx="8" cy="8" rx="3" ry="5.5" />
      <path d="M1.75 8h3.25M11 8h3.25" />
    </Glyph>
  );
}

/** Attention heads: a grid of cells. */
export function IconHeads(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <rect x="2.5" y="2.5" width="4.5" height="4.5" rx="0.75" />
      <rect x="9" y="2.5" width="4.5" height="4.5" rx="0.75" />
      <rect x="2.5" y="9" width="4.5" height="4.5" rx="0.75" />
      <rect x="9" y="9" width="4.5" height="4.5" rx="0.75" fill="currentColor" />
    </Glyph>
  );
}

/** A sparse feature: one bar lit among many. */
export function IconFeature(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M3 13.25v-1.5M5.5 13.25v-1M8 13.25v-9M10.5 13.25v-2M13 13.25v-1" />
    </Glyph>
  );
}

/** Steer: push a direction. */
export function IconSteer(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 8 11 5M11 5H8.75M11 5v2.25" />
    </Glyph>
  );
}

/** An axis with a marker on it. */
export function IconAxis(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M2.5 13.5 13.5 2.5" />
      <path d="M2.5 13.5h3M2.5 13.5v-3" />
      <circle cx="9.5" cy="6.5" r="1.25" fill="currentColor" stroke="none" />
    </Glyph>
  );
}

/** A graph of nodes and edges — a circuit. */
export function IconGraph(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="4" cy="12" r="1.5" />
      <circle cx="12" cy="12" r="1.5" />
      <circle cx="8" cy="4" r="1.5" />
      <path d="M7.2 5.3 4.8 10.7M8.8 5.3l2.4 5.4M5.5 12h5" />
    </Glyph>
  );
}

/** Words out of a vector — natural-language readout. */
export function IconSpeech(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M2.75 3.25h10.5v7H7.5l-3 2.5v-2.5H2.75Z" />
      <path d="M5 6h6M5 8h4" />
    </Glyph>
  );
}

/** A shelf of stored artifacts. */
export function IconLibrary(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M3 2.75v10.5M6 2.75v10.5" />
      <path d="M8.75 3.4 11 2.9l2.25 10.25-2.25.5Z" />
      <path d="M2 13.25h12" />
    </Glyph>
  );
}

/** Icons addressable by name, for declarations that can only carry a string
 *  (a `SectionDecl.glyph`). */
export const NAMED_GLYPHS: Readonly<Record<string, (props: SVGProps<SVGSVGElement>) => ReactElement>> =
  {
  home: IconHome,
  layers: IconLayers,
  timeline: IconTimeline,
  cube: IconCube,
  attention: IconAttention,
  pencil: IconPencil,
  wave: IconWave,
  step: IconStep,
  lens: IconLens,
  heads: IconHeads,
  feature: IconFeature,
  steer: IconSteer,
  axis: IconAxis,
  graph: IconGraph,
  speech: IconSpeech,
  library: IconLibrary,
  search: IconSearch,
  list: IconList,
};
