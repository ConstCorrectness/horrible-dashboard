/**
 * Vector stroke icons for the trajectories pane.
 *
 * Inline SVG rather than an icon package: this repo has no icon dependency and no
 * Tailwind, and adding two npm packages for one pane is a bigger decision than a
 * pane should make on its own. What matters for the house style is that these are
 * stroke vectors that inherit `currentColor` — not native emoji, which render as
 * a different picture on every OS and carry no weight or colour.
 *
 * The pane manifest's `icon:` field is a different thing: it is the activity-rail
 * glyph, typed as a string, and every module in the app puts an emoji there. That
 * one follows the rail's convention.
 */

interface IconProps {
  size?: number;
  className?: string;
}

function svg(path: React.ReactNode, { size = 14 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flexShrink: 0 }}
    >
      {path}
    </svg>
  );
}

export const RouteIcon = (p: IconProps) =>
  svg(
    <>
      <circle cx="6" cy="19" r="3" />
      <circle cx="18" cy="5" r="3" />
      <path d="M9 19h5a4 4 0 0 0 0-8H9a4 4 0 0 1 0-8h6" />
    </>,
    p,
  );

export const CheckIcon = (p: IconProps) => svg(<path d="M20 6 9 17l-5-5" />, p);

export const XIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </>,
    p,
  );

export const CircleIcon = (p: IconProps) => svg(<circle cx="12" cy="12" r="8" />, p);

export const LockIcon = (p: IconProps) =>
  svg(
    <>
      <rect x="4" y="11" width="16" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </>,
    p,
  );

export const TerminalIcon = (p: IconProps) =>
  svg(
    <>
      <path d="m5 8 4 4-4 4" />
      <path d="M13 16h6" />
    </>,
    p,
  );

export const MessageIcon = (p: IconProps) =>
  svg(<path d="M21 15a2 2 0 0 1-2 2H8l-4 4V5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z" />, p);

export const BrainIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M12 5a3 3 0 0 0-6 0 3 3 0 0 0-1 5.8A3 3 0 0 0 8 16h4z" />
      <path d="M12 5a3 3 0 0 1 6 0 3 3 0 0 1 1 5.8A3 3 0 0 1 16 16h-4z" />
      <path d="M12 5v14" />
    </>,
    p,
  );

export const EyeIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" />
      <circle cx="12" cy="12" r="3" />
    </>,
    p,
  );

export const TrophyIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M7 4h10v5a5 5 0 0 1-10 0z" />
      <path d="M7 6H4v1a3 3 0 0 0 3 3M17 6h3v1a3 3 0 0 1-3 3" />
      <path d="M10 19h4M12 14v5" />
    </>,
    p,
  );

export const AlertIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M12 3 2 20h20z" />
      <path d="M12 10v4M12 17h.01" />
    </>,
    p,
  );

export const DatabaseIcon = (p: IconProps) =>
  svg(
    <>
      <ellipse cx="12" cy="6" rx="8" ry="3" />
      <path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
      <path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
    </>,
    p,
  );

export const RecordIcon = (p: IconProps) =>
  svg(
    <>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="4" fill="currentColor" stroke="none" />
    </>,
    p,
  );

export const ScaleIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M12 3v18M7 21h10" />
      <path d="M5 7h14" />
      <path d="m5 7-3 6h6zM19 7l-3 6h6z" />
    </>,
    p,
  );

export const RefreshIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
      <path d="M3 21v-5h5" />
    </>,
    p,
  );

export const TrashIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M4 7h16M10 11v6M14 11v6" />
      <path d="M6 7l1 13h10l1-13" />
      <path d="M9 7V4h6v3" />
    </>,
    p,
  );
