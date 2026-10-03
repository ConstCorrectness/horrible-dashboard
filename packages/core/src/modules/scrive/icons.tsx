/**
 * Vector stroke icons for the Scrive panes — inline SVG that inherits
 * `currentColor`, per the house rule against native emoji inside a pane. The
 * manifest's `icon:` is the activity-rail glyph and follows the rail's convention.
 */

interface IconProps {
  size?: number;
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

export const PlusIcon = (p: IconProps) => svg(<path d="M12 5v14M5 12h14" />, p);

export const RefreshIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M20 11a8 8 0 1 0-2.3 5.7" />
      <path d="M20 4v7h-7" />
    </>,
    p,
  );

export const PostIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M6 3h9l3 3v15H6z" />
      <path d="M9 10h6M9 14h6M9 18h3" />
    </>,
    p,
  );

export const PageIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M6 3h12v18H6z" />
      <path d="M9 8h6" />
    </>,
    p,
  );

export const NotebookIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M5 4h14v16H5z" />
      <path d="M9 4v16M12 9l3 2-3 2" />
    </>,
    p,
  );

export const SaveIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M5 4h11l3 3v13H5z" />
      <path d="M8 4v5h7V4M8 20v-6h8v6" />
    </>,
    p,
  );

export const BackRefIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M9 14 4 9l5-5" />
      <path d="M4 9h10a6 6 0 0 1 0 12h-3" />
    </>,
    p,
  );

export const SortDownIcon = (p: IconProps) => svg(<path d="M6 9l6 6 6-6" />, p);

export const SortUpIcon = (p: IconProps) => svg(<path d="M6 15l6-6 6 6" />, p);

export const BoardIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M4 4h4v16H4zM10 4h4v10h-4zM16 4h4v13h-4z" />
    </>,
    p,
  );

export const CloseIcon = (p: IconProps) => svg(<path d="M6 6l12 12M18 6 6 18" />, p);

export const CheckIcon = (p: IconProps) => svg(<path d="M5 12.5 10 17 19 7" />, p);

/** The agent: a four-point spark. */
export const SparkIcon = (p: IconProps) =>
  svg(
    <path d="M12 3c.6 4.2 2.8 6.4 7 7-4.2.6-6.4 2.8-7 7-.6-4.2-2.8-6.4-7-7 4.2-.6 6.4-2.8 7-7z" />,
    p,
  );

export const OutlineIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M4 6h3M10 6h10M4 12h3M10 12h10M6 18h1M10 18h10" />
    </>,
    p,
  );

/** Publish: an arrow leaving a tray. */
export const PublishIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M12 15V4M7.5 8.5 12 4l4.5 4.5" />
      <path d="M4 14v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" />
    </>,
    p,
  );

/** Opens somewhere else: a box with an arrow out of its corner. */
export const ExternalIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M14 4h6v6M20 4l-9 9" />
      <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
    </>,
    p,
  );

export const FilmIcon = (p: IconProps) =>
  svg(
    <>
      <rect x="3" y="4" width="18" height="16" rx="1" />
      <path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" />
    </>,
    p,
  );

/** Live co-editing: two people. */
export const LiveIcon = (p: IconProps) =>
  svg(
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
      <path d="M16 5.2a3.2 3.2 0 0 1 0 5.6M17.5 13.6A5.5 5.5 0 0 1 20.5 19" />
    </>,
    p,
  );
